Docker usage for library-http-server

Build (local):

```bash
# Build the Docker image
docker build -t library-http-server:latest .

# Run the image and map port 1337
docker run --rm -p 1337:1337 library-http-server:latest
```

With docker-compose:

```bash
docker-compose up --build
```

Notes and tips

- The project `package.json` declares `"engines": { "node": ">=18.19.0" }`. The `Dockerfile` uses Node `18.19.0` to match this.
- Build runs `npm run build` which executes `tsc && mcp-build`. Ensure `mcp-build` is available (it should be provided by the installed `mcp-framework` dependency). If the build fails because `mcp-build` is not found, you can:
  - Install `mcp-build` or the appropriate tool as a devDependency, or
  - Pre-build locally and use the prebuilt `dist/` when building the image.

Transport / port configuration

- The repository's `src/index.ts` currently constructs an `MCPServer` with `stdio` transport by default. The file includes a commented `http-stream` transport example which uses port `1337`.
- If you want the container to serve HTTP, edit `src/index.ts` to enable the `http-stream` transport (uncomment and/or configure) before building the image, then use the `docker run -p 1337:1337 ...` commands above.

Testing

- Testing the Docker build requires Docker installed on your machine.
- After building, `docker logs` or attaching to the container will show server output. If you enable HTTP transport, you can curl `http://localhost:1337` to interact.
