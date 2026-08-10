# Security policy

## Reporting a vulnerability

This is a private, personal deployment. Do not put passwords, subscription
tokens, Agent tokens, UUIDs, node URIs, or personalized subscription URLs in
issues, pull requests, logs, screenshots, or public messages. Report a
suspected vulnerability to the project owner through a private channel and
include only the minimum reproducible details.

## Deployment hardening checklist

The repository contains the application code, but several controls belong to
the deployment and must be verified on each VPS:

- Run FastAPI as a dedicated unprivileged service account. Keep proxy reload
  operations in a narrowly scoped root-only helper.
- Make the SQLite database and environment files readable only by the service
  account or root. Never serve the database directory through the web server.
- Do not record `X-Sub-App-Agent-Token` or subscription URLs in Caddy,
  reverse-proxy, or application access logs. Rotate tokens after an exposure.
- Keep the origin protected by an origin firewall or an equivalent control;
  Cloudflare Access alone is not a substitute for origin restriction.
- Keep public `/healthz` limited to liveness. Use authenticated
  `/api/admin/healthz` for operational details.
- Treat subscription tokens as bearer credentials and preserve the response
  `Cache-Control: private, no-store` behavior.
- Back up proxy configuration before a credential rollout and verify config
  validation, reload, external connectivity, and rollback before removing old
  credentials.
