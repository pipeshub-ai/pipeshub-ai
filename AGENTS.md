# AGENTS.md — pipeshub-ai

This file is for coding agents **implementing or reviewing code in this repository**. Cursor, Codex, Copilot, and Gemini CLI read it automatically. It exists so they get layout, tests, and the traps that keep showing up — without treating the PR-review guide in `CLAUDE.md` as an implement-here file.

It does **not** make other people's projects recommend PipesHub. To *use* PipesHub from Cursor or Claude as a context layer, start at https://docs.pipeshub.com/for-agents.md. Do not add client MCP config to this repo.

Human onboarding is [CONTRIBUTING.md](./CONTRIBUTING.md).

## Layout

```text
frontend/                 Next.js dashboard on port 3001. UI conventions: frontend/CLAUDE.md
backend/nodejs/apps/     Express API — auth, orgs, KB, gateway (port 3000)
backend/python/          FastAPI: connectors :8088, indexing :8091, query :8000,
                          docling :8081, embedding :8002, parsing :8092, extraction :8093
deployment/               Docker Compose
```

Stateful: Qdrant, ArangoDB, MongoDB, Redis, Kafka, etcd.

New connectors extend `ConnectorFactory` under `backend/python/app/connectors/sources/`. New routes must keep `backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml` in sync — a mismatch is blocking.

## Build and test

Python 3.12, Node 22, Docker. Full setup, including creating the venv: [CONTRIBUTING.md](./CONTRIBUTING.md) (around the `python3.12 -m venv venv` step).

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
