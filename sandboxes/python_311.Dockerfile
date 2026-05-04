FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    patch \
    build-essential \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade \
    pip \
    pytest \
    pytest-asyncio \
    pytest-xdist \
    pytest-timeout \
    pytest-mock \
    coverage \
    tox \
    virtualenv

RUN useradd -m -u 1000 -s /bin/bash sandbox
RUN mkdir -p /workspace && chown -R sandbox:sandbox /workspace /tmp

USER sandbox
ENV HOME=/workspace
ENV WORKSPACE=/workspace
ENV PATH="/workspace/.local/bin:${PATH}"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

RUN git config --global user.email "runner@swebench.org" && \
    git config --global user.name "SWE-Bench Runner" && \
    git config --global --add safe.directory /workspace

WORKDIR /workspace

CMD ["sleep", "infinity"]
