FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Non-root user.
RUN useradd -m -u 1000 agent
WORKDIR /app

COPY requirements-ui.txt .
RUN pip install -r requirements-ui.txt

COPY app ./app

USER agent
EXPOSE 8000

# The chat UI only talks to an A2A agent. Point it at one with A2A_AGENT_URL (and A2A_AUTH_MODE for AgentCore).
# All configuration comes from environment variables (see .env.example).
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]
