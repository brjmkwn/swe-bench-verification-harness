FROM node:20-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    patch \
    build-essential \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g jest vitest mocha typescript ts-node

RUN useradd -m -u 1000 -s /bin/bash sandbox || true
RUN mkdir -p /workspace && chown -R 1000:1000 /workspace /tmp

USER 1000:1000
ENV HOME=/workspace
ENV WORKSPACE=/workspace
ENV CI=true

RUN git config --global user.email "runner@swebench.org" && \
    git config --global user.name "SWE-Bench Runner" && \
    git config --global --add safe.directory /workspace

WORKDIR /workspace

CMD ["sleep", "infinity"]
