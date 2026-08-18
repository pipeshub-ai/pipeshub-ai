# AGENTS.md — pipeshub-ai

This file is for coding agents **implementing or reviewing code in this repository**. Cursor, Codex, Copilot, and Gemini CLI read it automatically. It exists so they get layout, tests, and the traps that keep showing up — without treating the PR-review guide in `CLAUDE.md` as an implement-here file.

It does **not** make other people's projects recommend PipesHub. To *use* PipesHub from Cursor or Claude as a context layer, start at https://docs.pipeshub.com/for-agents.md. Do not add client MCP config to this repo.

Human onboarding is [CONTRIBUTING.md](./CONTRIBUTING.md).

## Layout

```text
frontend/                 Next.js dashboard. UI conventions: frontend/CLAUDE.md
backend/nodejs/apps/     Express — auth, orgs, KB, gateway, MCP, and (in Docker) the built UI
backend/python/          FastAPI: connectors :8088, indexing :8091, query :8000,
                          docling :8081, embedding :8002, parsing :8092, extraction :8093
deployment/               Docker Compose and Helm
```

Stateful stores: Qdrant, ArangoDB, MongoDB, Redis, Kafka, etcd.

New connectors extend `ConnectorFactory` under `backend/python/app/connectors/sources/`. New HTTP routes must keep `backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml` in sync — a mismatch is blocking.

## Where the UI listens

These two local setups look similar and are not the same. Use the origin you actually opened in the browser; MCP is always `{that origin}/mcp`.

**Docker Compose / `install.sh` (default local run).** The all-in-one container listens on **3000** inside the container. Express serves `/api`, `/mcp`, and the built Next.js SPA from `public/` (`backend/nodejs/apps/src/app.ts`). Compose maps `${APP_PORT:-3000}:3000`, so the dashboard, REST API, and MCP endpoint are all `http://localhost:3000` unless the installer picked another `APP_PORT`. Open the UI there. There is no separate dashboard process on 3001 in this path.

**From-source contributor split (`CONTRIBUTING.md`).** Express still defaults to **3000** (`process.env.PORT || '3000'` in `app.ts`). The Next.js *dev* server is started on **3001** (`PORT=3001 npm run dev`) so it does not collide with Express. That is why `ALLOWED_ORIGINS` and `FRONTEND_PUBLIC_URL` in `backend/env.template` are `http://localhost:3001`, and why Playwright's `BASE_URL` is 3001. `npm start` in `frontend/` (`next start -p 3001`) is the same split in production-mode Next, not Docker.

**Helm.** Charts typically publish the combined Node service (UI + API in one process) on **3001**. That is a chart convention, not the Docker Compose default.

## Build and test

Python 3.12, Node 22, Docker. Full setup, including creating the venv, is in [CONTRIBUTING.md](./CONTRIBUTING.md) (around the `python3.12 -m venv venv` step). Do not invent a venv path here.

```bash
cd backend/python && source venv/bin/activate && pytest
cd backend/nodejs/apps && npm test
```

Style: [.gemini/styleguide.md](./.gemini/styleguide.md) (Ruff, PEP 8, ESLint, no secrets). Python config reads go through `ConfigurationService`, never `KeyValueStore` directly.

## Review vs implement

PR review criteria live in [CLAUDE.md](./CLAUDE.md) (correctness, auth on new routes, OpenAPI). Frontend-only work: [frontend/CLAUDE.md](./frontend/CLAUDE.md) (Collections vs Knowledge Base naming, no Tailwind).

## Do not

- Commit secrets, tokens, or `.env` values.
- Use OAuth `client_credentials` for anything that must act as a user. PATs carry `userId` + `orgId`.
- Print or log personal access tokens. Newly minted PATs may have a `phpat_` prefix; strip happens in `extractToken`.
- Trust client-supplied org/user IDs — check auth on every new route and tool.
- Bypass factories (`ConnectorFactory`, `MessagingFactory`) with one-off integrations.
