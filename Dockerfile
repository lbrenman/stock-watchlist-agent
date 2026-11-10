FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Non-root user with a writable home (the bundled CLI keeps a little state under ~).
RUN useradd -m -u 1000 agent
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

USER agent
EXPOSE 8000

# All configuration comes from environment variables (see .env.example).
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]
