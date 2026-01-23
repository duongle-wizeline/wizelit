# Config Authentication

Step 1: Clone the .env.template and rename to .env
Step 2: Run command

```
chainlit create-secret
```

Step 3: Copy the chainlit secret into .env and update other environment variables

# Config Data Persistent

Step 1: Update POSTGRES_xxx variables in .env file correctly
Step 2: Start postgres container by using command

```
docker-compose up -d
```

Step 3: Restart server to apply tables to database

# Real-Time Log Streaming (NEW ⚡)

The system now supports **real-time log streaming** from workers to the hub via Redis Pub/Sub and PostgreSQL persistence:

- ✅ **Push-based delivery** - No polling overhead
- ✅ **Persistent storage** - Jobs survive restarts
- ✅ **Sub-100ms latency** - Real-time UI updates
- ✅ **Automatic fallback** - Works with or without Redis

## Quick Setup

```bash
# 1. Start Redis
docker-compose up -d redis

# 2. Install dependencies
make setup

# 3. Initialize database tables
make init-streaming

# 4. Configure environment (.env)
REDIS_URL=redis://localhost:6379
ENABLE_LOG_STREAMING=true
```

📖 See [STREAMING_MIGRATION_GUIDE.md](STREAMING_MIGRATION_GUIDE.md) for detailed setup instructions.

# MCP Server Integration

The Chainlit agent expects an MCP server that exposes its tools over HTTP:

- Default endpoint: `http://localhost:1337/mcp`
- Configure with the `MCP_SERVER_URL` environment variable
- Optional timeout override: `MCP_HTTP_TIMEOUT` (seconds)

MCP servers are now hosted in the separate [`sample-mcp-servers`](https://github.com/your-org/sample-mcp-servers) repository:

## Available MCP Servers

### 1. Code Scout MCP Server

Fast synchronous symbol scanner for Python codebases.

- Repository: `sample-mcp-servers/mcp_servers/code-scout/`
- Tools: scan_directory, find_symbol, analyze_impact, grep_search, git_blame, build_dependency_graph
- Supports local paths and GitHub URLs
- Set `GITHUB_TOKEN` for private repositories

### 2. Refactoring Agent MCP Server

AI-powered code refactoring using CrewAI and AWS Bedrock with **real-time log streaming**.

- Repository: `sample-mcp-servers/mcp_servers/refactoring-agent/`
- Tools: start_refactoring_job, get_job_status
- Requires AWS credentials with Bedrock access
- **Feature**: Real-time progress via Redis Pub/Sub + PostgreSQL persistence

See the [`sample-mcp-servers` README](https://github.com/your-org/sample-mcp-servers/blob/main/README.md) for detailed setup and usage instructions.
