# --- build the read-only GitHub MCP server binary (no Docker-in-Docker at runtime) ---
FROM golang:1.25-bookworm AS mcp
# Pin to a known release tag of github/github-mcp-server (verify the latest at
# https://github.com/github/github-mcp-server/releases before building).
ARG GITHUB_MCP_VERSION=v1.4.0
RUN go install github.com/github/github-mcp-server/cmd/github-mcp-server@${GITHUB_MCP_VERSION}

# --- runtime ---
FROM python:3.12-slim
COPY --from=mcp /go/bin/github-mcp-server /usr/local/bin/github-mcp-server
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
# Use the bundled binary, not docker; agentmemory off unless AGENTMEMORY_URL is set.
ENV BABBLA_GITHUB_MCP=binary \
    BABBLA_CONFIG=/data/channels.yaml \
    BABBLA_DB=/data/babbla.db \
    AGENTMEMORY_URL=""
VOLUME ["/data"]
ENTRYPOINT ["python", "-m", "babbla.app"]
