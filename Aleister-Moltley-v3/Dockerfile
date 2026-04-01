FROM python:3.12-slim AS base

# System deps for MCP stdio transport, git, and general tooling
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl jq ripgrep tree nodejs npm ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd -m -s /bin/bash aleister
WORKDIR /app

# Install Python deps (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Optional heavy deps — uncomment what you need:
# RUN pip install --no-cache-dir langchain langchain-ollama langchain-community chromadb pypdf
# RUN pip install --no-cache-dir tweepy
# RUN pip install --no-cache-dir PyGithub

COPY . .

# Data directories
RUN mkdir -p /data/memory /data/sessions /data/experience /data/conscience /data/batch_tasks /data/soul/impressions /data/soul/encounters /data/soul/world /data/soul/journal /data/palazzo \
    && chown -R aleister:aleister /data /app

USER aleister

# Environment defaults
ENV COMPAGNON_DATA_DIR=/data \
    COMPAGNON_MEMORY_DIR=/data/memory \
    COMPAGNON_SESSION_DIR=/data/sessions \
    DASHBOARD_PORT=8080 \
    PYTHONUNBUFFERED=1

EXPOSE 8080 8443

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:${DASHBOARD_PORT}/health || exit 1

# Default: run the bot. Override with "bootstrap-report" etc for diagnostics.
ENTRYPOINT ["python", "main.py"]
CMD ["run"]
