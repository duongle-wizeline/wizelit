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

# MCP Server Integration

The Chainlit agent expects an MCP server that exposes its tools over HTTP:

- Default endpoint: `http://localhost:1337/mcp`
- Configure with the `MCP_SERVER_URL` environment variable
- Optional timeout override: `MCP_HTTP_TIMEOUT` (seconds)

To run the reference server included in this repo:

```
cd mcp_servers/library-http-server
npm install
npm run build
node dist/index.js
```

Once the server is running, Chainlit will automatically discover the available MCP tools and expose them to the internal LLM.