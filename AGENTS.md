# AGENTS.md — pipeshub-ai

PipesHub is a self-hosted workplace AI platform: connectors, permissioned search, knowledge graph, agents. Human onboarding is [CONTRIBUTING.md](./CONTRIBUTING.md). This file is for coding agents working **in this repository**.

To **use** PipesHub from Cursor/Claude/Gemini as a context layer (not to contribute here), start at https://docs.pipeshub.com/for-agents.md — do not add client MCP config to this repo.

## Layout

```text
frontend/                 Next.js dashboard. UI conventions: frontend/CLAUDE.md
backend/nodejs/apps/     Express API — auth, orgs, KB, gateway (port 3001)
backend/python/          FastAPI: connectors :8088, indexing :8091, query :8000,
                          docling :8081, embedding :8002
deployment/               Docker Compose
```

Stateful: Qdrant, ArangoDB, MongoDB, Redis, Kafka, etcd.

New connectors extend `ConnectorFactory` under `backend/python/app/connectors/sources/`. New routes must keep `backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml` in sync — a mismatch is blocking.

## Build and test

Python 3.12, Node 22, Docker. Full setup: CONTRIBUTING.md.

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
