#!/usr/bin/env bash
# Build the ARM64 A2A image and push it to ECR. Prints the image URI.
# Needs: docker with buildx, aws CLI with credentials for the target account.
#   AWS_REGION=us-east-1 bash deploy/build_and_push.sh
set -euo pipefail

: "${AWS_REGION:?Set AWS_REGION}"
ECR_REPO="${ECR_REPO:-stock-watchlist-agent}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
IMAGE="${REGISTRY}/${ECR_REPO}:${IMAGE_TAG}"

aws ecr describe-repositories --repository-names "${ECR_REPO}" --region "${AWS_REGION}" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "${ECR_REPO}" --region "${AWS_REGION}" >/dev/null

aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${REGISTRY}"

docker buildx build --platform linux/arm64 -f Dockerfile.agentcore -t "${IMAGE}" --push .

echo "${IMAGE}"
