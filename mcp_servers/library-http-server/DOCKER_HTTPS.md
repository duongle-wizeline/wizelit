HTTPS via nginx reverse-proxy

This project now includes an `nginx` service in `docker-compose.yml` that terminates TLS and proxies to the application container.

Files added/used:
- `nginx.conf` — nginx configuration that listens on 443 (and redirects 80 -> 443) and proxies to the app on port 1337.
- `localhost+2.pem` and `localhost+2-key.pem` — self-signed certificate and key (already in repo root). They are mounted into the nginx container.

Quick start (insecure, for local testing):

```bash
# Start services
docker compose up --build -d

# Test HTTPS (insecure due to self-signed cert)
curl -vk https://localhost/
```

Notes:
- The app is reachable at `https://localhost/` and nginx will proxy requests to the app's `/mcp` endpoint.
- Because the certs are self-signed, `curl -k` is necessary unless you add the certificate to your OS trust store.
- If you prefer the app to terminate TLS directly, you'd need to modify `src/index.ts` to accept TLS options (not required with this reverse-proxy approach).
