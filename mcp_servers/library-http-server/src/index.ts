import { MCPServer } from "mcp-framework";

const server = new MCPServer({
  transport: {
    // type: "stdio",
    type: "http-stream",
    options: {
      port: 1337,
      cors: {
        allowOrigin: "*",
        allowMethods: "GET, POST, DELETE, OPTIONS",
        allowHeaders: "Content-Type, Accept, Authorization, x-api-key, Mcp-Session-Id, Last-Event-ID",
        exposeHeaders: "Content-Type, Authorization, x-api-key, Mcp-Session-Id",
        maxAge: "86400"
      },
      // auth: {                    // Authentication configuration
      //   provider: authProvider
      // },
      session: {                 // Session configuration
        enabled: false,           // Enable session management (default: true)
        headerName: "Mcp-Session-Id", // Session header name (default: "Mcp-Session-Id")
        allowClientTermination: true  // Allow clients to terminate sessions (default: true)
      },
    },
  }});

await server.start();