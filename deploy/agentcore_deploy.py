"""Create (or update) the AgentCore Runtime for the A2A agent.

Prerequisites
  * An ARM64 image in ECR (deploy/build_and_push.sh prints its URI).
  * An IAM execution role for the runtime (see the AgentCore docs, "Runtime permissions").
  * AWS credentials in your shell with permission to call bedrock-agentcore-control.

Usage
  python deploy/agentcore_deploy.py --image <ecr-uri> --role-arn <role-arn> --region us-east-1
  python deploy/agentcore_deploy.py --image <ecr-uri> --role-arn <role-arn> --update <runtime-id>

Runtime configuration values (model route, gateway URLs, keys) are read from YOUR shell
environment (same names as .env.example) and set as the runtime's environment variables.
Those values are stored in the runtime's configuration, so prefer short-lived demo keys,
and move them to AWS Secrets Manager before anything beyond a demo.

Auth for callers: IAM (SigV4) by default. Pass --jwt-discovery-url and --jwt-allowed-client
to use a JWT authorizer instead (callers then send Authorization: Bearer ...).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from urllib.parse import quote

import boto3

# Environment variables copied from your shell into the runtime, when set.
RUNTIME_ENV_NAMES = [
    "MODEL_BASE_URL", "MODEL_NAME", "MODEL_AUTH_MODE", "MODEL_API_KEY", "MODEL_DISABLE_BETAS",
    "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
    "MCP_URL", "MCP_TRANSPORT", "MCP_SERVER_NAME", "MCP_AUTH_MODE", "MCP_API_KEY_HEADER", "MCP_API_KEY",
    "AGENT_MAX_TURNS", "AGENT_API_KEYS", "AGENT_REQUIRE_API_KEY",
]
# Request headers the A2A server reads (must be allowlisted or AgentCore drops them). A2A-Version is how an A2A 1.0
# caller says which protocol version it speaks; the agent is 1.0 only, so without it every call would fail.
BASE_HEADER_ALLOWLIST = ["X-Mcp-Key", "X-Model-Key", "X-Api-Key", "A2A-Version"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", required=True, help="ECR image URI (ARM64)")
    ap.add_argument("--role-arn", default=os.environ.get("AGENTCORE_ROLE_ARN"), help="runtime execution role ARN")
    ap.add_argument("--region", default=os.environ.get("AWS_REGION"))
    ap.add_argument("--name", default="stock_watchlist_agent",
                    help="runtime name (letters, digits and underscores; must start with a letter)")
    ap.add_argument("--update", metavar="RUNTIME_ID", help="update this existing runtime instead of creating one")
    ap.add_argument("--jwt-discovery-url", default=os.environ.get("AGENTCORE_JWT_DISCOVERY_URL"))
    ap.add_argument("--jwt-allowed-client", action="append", default=[], help="repeat for several clients")
    ap.add_argument("--no-wait", action="store_true", help="do not wait for the runtime to become READY")
    args = ap.parse_args()

    if not args.region:
        ap.error("set --region or AWS_REGION")
    if not args.role_arn:
        ap.error("set --role-arn or AGENTCORE_ROLE_ARN")
    if args.jwt_discovery_url and not args.jwt_allowed_client:
        ap.error("--jwt-discovery-url needs at least one --jwt-allowed-client")

    env_vars = {n: os.environ[n] for n in RUNTIME_ENV_NAMES if os.environ.get(n)}
    print("Runtime environment variables being set (names only):", ", ".join(sorted(env_vars)) or "(none)")
    if not env_vars:
        print("warning: none of the expected variables are set in your shell; the agent will not start.",
              file=sys.stderr)

    allowlist = list(BASE_HEADER_ALLOWLIST)
    params: dict = {
        "agentRuntimeArtifact": {"containerConfiguration": {"containerUri": args.image}},
        "roleArn": args.role_arn,
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "protocolConfiguration": {"serverProtocol": "A2A"},
        "environmentVariables": env_vars,
    }
    if args.jwt_discovery_url:
        allowlist.append("Authorization")
        params["authorizerConfiguration"] = {
            "customJWTAuthorizer": {
                "discoveryUrl": args.jwt_discovery_url,
                "allowedClients": args.jwt_allowed_client,
            }
        }
    params["requestHeaderConfiguration"] = {"requestHeaderAllowlist": allowlist}

    control = boto3.client("bedrock-agentcore-control", region_name=args.region)
    if args.update:
        resp = control.update_agent_runtime(agentRuntimeId=args.update, **params)
        runtime_id = resp.get("agentRuntimeId", args.update)
    else:
        resp = control.create_agent_runtime(agentRuntimeName=args.name, **params)
        runtime_id = resp["agentRuntimeId"]
    arn = resp["agentRuntimeArn"]
    print(f"Runtime: {runtime_id}\nARN:     {arn}")

    if not args.no_wait:
        print("Waiting for the runtime to become READY ...")
        for _ in range(60):
            status = control.get_agent_runtime(agentRuntimeId=runtime_id)["status"]
            print(f"  status: {status}")
            if status == "READY":
                break
            if "FAILED" in status:
                print("The runtime failed to start. Check its CloudWatch logs.", file=sys.stderr)
                return 1
            time.sleep(10)
        else:
            print("Timed out waiting for READY; check the AgentCore console.", file=sys.stderr)
            return 1

    url = f"https://bedrock-agentcore.{args.region}.amazonaws.com/runtimes/{quote(arn, safe='')}/invocations/"
    auth = "a JWT bearer token" if args.jwt_discovery_url else "AWS credentials (a SigV4 signature)"
    print("\nThe agent's A2A address (JSON-RPC is POSTed here; the card is at <address>.well-known/agent-card.json):")
    print(f"  {url}")
    print(f"Callers present {auth} to AgentCore, and also send x-api-key (one of your AGENT_API_KEYS).")
    print("The web page cannot call this runtime directly: a browser cannot sign SigV4 requests, and the page has no bearer-token field.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
