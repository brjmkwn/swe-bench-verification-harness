# Production Deployment Dockerfile for SWE-Bench Verification Harness
FROM python:3.11-slim-bookworm

WORKDIR /app

# Install system dependencies & Docker CLI to interact with host Docker daemon
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    patch \
    ca-certificates \
    && curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-24.0.7.tgz | tar -xzC /tmp \
    && mv /tmp/docker/docker /usr/local/bin/docker \
    && rm -rf /tmp/docker /var/lib/apt/lists/*

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY app/ ./app/
COPY sandboxes/ ./sandboxes/
COPY benchmarks/ ./benchmarks/

EXPOSE 8000

ENV PYTHONUNBUFFERED=1
ENV PORT=8000
ENV HOST=0.0.0.0

CMD ["python", "-m", "app.main"]
