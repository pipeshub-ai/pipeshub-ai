# AGENTS.md — pipeshub-ai

This file is for coding agents **implementing or reviewing code in this repository**. Cursor, Codex, Copilot and Gemini CLI read it automatically; Claude Code loads it through `CLAUDE.md`. It covers layout, tests, and the traps that keep showing up.

It does **not** make other people's projects recommend PipesHub. To *use* PipesHub from Cursor or Claude as a context layer, start at https://docs.pipeshub.com/for-agents.md. Do not add client MCP config to this repo.

Human onboarding is [CONTRIBUTING.md](./CONTRIBUTING.md).

## Layout

```text
frontend/                 Next.js dashboard. UI conventions: frontend/AGENTS.md
backend/nodejs/apps/     Express — auth, orgs, KB, gateway, MCP, and (in Docker) the built UI
backend/python/          FastAPI: connectors :8088, indexing :8091, query :8000,
                          docling :8081, embedding :8002, parsing :8092, extraction :8093
deployment/               Docker Compose and Helm
```

New connectors extend `ConnectorFactory` under `backend/python/app/connectors/sources/`. Before adding or changing one, read [CONNECTOR.md](./CONNECTOR.md); [CONNECTOR_CHECKLIST.md](./CONNECTOR_CHECKLIST.md) is the definition of done. New HTTP routes must keep `backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml` in sync — a mismatch is blocking.

Design docs for each feature are listed in [docs/README.md](./docs/README.md).

## Module guides

Some folders have their own `AGENTS.md`: what the code does, the rules it must keep, the decisions behind it, and what to check when you add, change or remove code there. **Before you edit a file, read the guide in its folder and in every parent folder up to this one.** Each guide has a `CLAUDE.md` next to it that only imports it (`@AGENTS.md`), so Claude Code loads the guide when it opens a file in that folder.

| Folder | Read before you change |
| --- | --- |
| [`frontend/`](./frontend/AGENTS.md) | Any UI: naming, routing, styling, i18n, data fetching |
| [`backend/nodejs/apps/src/libs/`](./backend/nodejs/apps/src/libs/AGENTS.md) | Node errors, calls to other services, retries |
| [`backend/nodejs/apps/src/libs/middlewares/`](./backend/nodejs/apps/src/libs/middlewares/AGENTS.md) | Error handling, path params, validation, access lines for any Node route |
| [`backend/nodejs/apps/src/modules/`](./backend/nodejs/apps/src/modules/AGENTS.md) | Any Node feature module, above all the ones that proxy to the Python services |
| [`backend/nodejs/apps/src/modules/user_management/`](./backend/nodejs/apps/src/modules/user_management/AGENTS.md) | Users, roles, admins, groups, teams, invites |
| [`backend/python/app/agents/mcp/`](./backend/python/app/agents/mcp/AGENTS.md) | The MCP client and its server catalog |
| [`backend/python/app/config/`](./backend/python/app/config/AGENTS.md) | Config reads and writes, KV providers, the file-type table |
| [`backend/python/app/connectors/api/`](./backend/python/app/connectors/api/AGENTS.md) | The connectors service HTTP API: OAuth, connector create and config |
| [`backend/python/app/services/graph_db/`](./backend/python/app/services/graph_db/AGENTS.md) | Graph queries and providers, and any job that reads the graph without a request |
| [`backend/python/app/services/messaging/`](./backend/python/app/services/messaging/AGENTS.md) | Broker consumers and producers, retries, commits |

### Writing and keeping a guide

- **Same PR.** A change that makes a guide wrong (a rule, a decision, a file or symbol it names, a checklist step) updates the guide in the same PR. Reviewers treat a stale guide as a defect.
- **Write one** for a folder whose rules an agent cannot see from the code: invariants, failure modes, decisions that look odd until you know why. Add it to the table above.
- **No guide where one is needed: ask, then write it.** If the folder you are working in fits the line above and neither it nor a parent below the root has a guide, ask the user whether to add one. If they agree, write it in the same PR.
- **A guide disagrees with the code: find out which is wrong.** If the code is right, correct the guide in your PR, even when your change did not cause the drift, and say so in the PR description. If the code breaks a rule or decision the guide records, tell the user before you change either.
- **A guide misses something important: add it.** A rule, failure mode or decision you had to learn the hard way (from a bug, a failing test or a review comment) goes into the guide, so the next agent does not learn it the same way.
- **Shape.** Content in `AGENTS.md`; a `CLAUDE.md` beside it that holds only `@AGENTS.md`. Sections: what it is and when to read it, how it fits, files, rules, decisions (with the PR), changing this code (adding / editing / removing), tests, keeping this file true. [`backend/python/app/agents/mcp/AGENTS.md`](./backend/python/app/agents/mcp/AGENTS.md) is a full example.
- **Only what holds on main**, checked in the code. Cite files and symbols, never line numbers. Link this file and `docs/` instead of repeating them.

## Storage is pluggable

Do not open a client for PipesHub's own stores or broker (MongoDB, Neo4j, Arango, PostgreSQL, Qdrant, Kafka) from indexing/query/connector feature code. Call `IGraphDBProvider`, `IVectorDBService`, `MessagingFactory`, or the Node repositories. Those factories read env and construct the vendor client; your code stays the same on a Neo4j, an Arango and a PostgreSQL box. A connector's client for the source it indexes (for example the PostgreSQL connector reaching the database it syncs) is not covered by this rule.

| Role | Env | Backends | Interface / factory |
| --- | --- | --- | --- |
| Graph | `DATA_STORE` | `neo4j` (what `install.sh`, Helm, and `backend/env.template` ship), `arangodb`, `postgres` (opt-in; schema `graph`) | `IGraphDBProvider` / `GraphDBProviderFactory`; connectors use `GraphDataStore` |
| Vector | `VECTOR_DB_TYPE` | `qdrant` (default), `opensearch`, `redis`, `pgvector` (opt-in; PostgreSQL with pgvector) | `IVectorDBService` / `VectorDBProviderFactory` |
| KV / config | `KV_STORE_TYPE` | `redis` (default), `etcd`, `postgres` | `KeyValueStore` / `KeyValueStoreFactory`. Python config reads go through `ConfigurationService`, never the store directly |
| Document | `DOC_STORE` | `mongo` (default), `postgres` (opt-in) | Repositories in the Node API (users, orgs, sessions, …): `backend/nodejs/apps/src/libs/repositories/<name>/` with `mongo.ts` and `pg.ts`, chosen by `store-select.ts`. Feature code uses the repository, never Mongoose or SQL directly |
| Blob | storage config | local, S3, Azure Blob | `StorageServiceInterface` (`backend/nodejs/apps/src/modules/storage/`) |
| Broker | `MESSAGE_BROKER` | `kafka`, `redis` (streams), `postgres` (queue tables) | `MessagingFactory` |

Redis can be KV, vector, and broker at once; that does not make it the graph or the document store. The PostgreSQL **connector** (a source to index) is separate from PostgreSQL as a store.

**A document change covers both document stores.** A new or changed repository method is implemented in its `mongo.ts` and `pg.ts` and passes the repository's shared contract suite on both (`backend/nodejs/apps/tests/stores/contract/`, run against Mongo and PostgreSQL). A new collection or table also needs its PostgreSQL migration under `backend/db/`.

**A vector change covers every vector store.** A new or changed `IVectorDBService` method is implemented for Qdrant, OpenSearch, Redis and pgvector (`backend/python/app/services/vector_db/`), or the PR says which store does not support it yet. Every store must still start: an `@abstractmethod` left unimplemented fails at construction. So either give the method a concrete default on `IVectorDBService` (as `memory_report`, `transient_search_error` and `reconcile_storage_layout` do), or implement it in the refusing store to raise an explicit error, with a test for that error.

**A graph change covers every graph store.** A new or changed `IGraphDBProvider` method, or a new graph query, is implemented for Neo4j, ArangoDB and PostgreSQL (`backend/python/app/services/graph_db/{neo4j,arango,postgres}/`) and tested on each: real-store tests in `backend/python/tests/integration/graph_db/`, with the PostgreSQL ones in its `postgres/` folder. Where PostgreSQL cannot serve a method yet, it raises `NotImplementedInPostgres` (`graph_db/postgres/base.py`) instead of returning a partial answer, a test pins that it raises, the Neo4j and ArangoDB implementations are still tested, and the PR says so.

## What lives where

**Node.js** (`backend/nodejs/apps/`): identity (users, orgs, auth — JWT, OAuth, PAT), knowledge-base metadata in Mongo, blob upload/download, HTTP API gateway, MCP at `/mcp`, Kafka/Redis producers for work the Python services consume.

**Python** (`backend/python/`):

- **Connectors** `:8088` (`app.connectors_main`) — OAuth, token refresh, sync from Slack/Drive/Jira/… into the graph. New sources extend `ConnectorFactory`.
- **Indexing** `:8091` (`app.indexing_main`) — parse, chunk, embed; write records through `IGraphDBProvider` and `IVectorDBService`. How it works end to end (consumer loop, heavy/light tiers, gates, leases, governor, recovery, known throughput-collapse root cause): [docs/indexing-service.md](./docs/indexing-service.md). Read it before touching `app/services/messaging/`, `app/services/resource_governor/`, `app/events/`, `app/modules/transformers/`, or `app/modules/pipeline/`.
- **Agent context** (what each model call receives) — history replay, the current request, the PRE_MODEL shaping pipeline, protection and the final guard: [docs/context-engineering.md](./docs/context-engineering.md). Read it before touching `app/agent_loop_lib/context/`, `app/agent_loop_lib/hooks/middleware/builtin/`, `app/agents/agent_loop/history_window.py`, history seeding in `factory.py`, `converters.py` or the transports. The kernel's core types, hook pipeline and transports now live in `backend/python/harness_core/` (`kernel/`, `models/transports/`); their old `app.agent_loop_lib` paths are import shims, so change the `harness_core` module.
- **Text fragments** (`#:~:text=` citation links) — built only by `app/utils/text_fragments/`; do not hand-roll a fragment or strip one with a regex. Design, encoding rules, golden corpus and browser verification: [docs/text-fragments.md](./docs/text-fragments.md). Read it before touching `app/utils/text_fragments/`, the fragment call sites in `chat_helpers.py`/`citations.py`/`tool_handlers.py`, or `slack-bot/src/utils/citations.ts`.
- **Query** `:8000` (`app.query_main`) — semantic search, RAG/chat, in-product agents, LLM orchestration (LiteLLM). Configured-model discovery, health checks, and the OpenAI-compatible model catalog: [docs/ai-models.md](./docs/ai-models.md). Identity (user and groups) forwarded to an LLM gateway: [docs/llm-gateway-identity.md](./docs/llm-gateway-identity.md). Read it before touching `app/services/model_identity/` or adding a model call outside a route that binds a call context.
- **Docling** `:8081` (`app.docling_main`) — heavy PDF/OCR for complex documents.
- **Embedding** `:8002` (`app.embedding_main`) — local HuggingFace / SentenceTransformer embeddings, OpenAI-compatible `/v1/embeddings`.
- **Parsing** `:8092` (`app.parsing_main`) — file bytes → `BlocksContainer` JSON.
- **Extraction** `:8093` (`app.extraction_main`) — `BlocksContainer` → `SemanticMetadata` (LLM classification). The indexing orchestrator calls this; it does not hold its own graph connection.

**Agent harness** (durable runs and coding engines; design in [docs/agent-harness-v2/ARCHITECTURE.md](./docs/agent-harness-v2/ARCHITECTURE.md), decisions in [docs/agent-harness-v2/adr/](./docs/agent-harness-v2/adr/)). The server switch `AGENT_HARNESS_WORKER_ENABLED` (on by default; Compose `.env`, `PIPESHUB_AGENT_HARNESS=on|off ./install.sh`, Helm `agentHarness.enabled`) starts the harness processes idle; an org admin then turns on Labs **Agent harness** (off by default). Off, no harness process, route, Labs flag or UI exists and every chat takes the classic path. Gate anything a person or an org sees on the org predicate, the switch and the org's Labs flag (Node `orgHarnessOn`, routers behind `whenHarnessSwitchedOn`, Python `is_agent_harness_on`, browser `selectAgentHarnessOn`; [ADR-OPS-08](./docs/agent-harness-v2/adr/ADR-OPS-08-one-org-harness-predicate.md)); only process start-up keys on the switch alone (`agentHarnessSwitchedOn`). What it covers and what changed for every install: [docs/agent-harness-v2/guides/HARNESS-OFF.md](./docs/agent-harness-v2/guides/HARNESS-OFF.md), [ADR-OPS-06](./docs/agent-harness-v2/adr/ADR-OPS-06-one-harness-switch-off-by-default.md). None of these ports is published: only the app's `3000` is. Sandboxes reach the two gateways only through the egress proxy, as the aliases on the internal `sandbox_plane` network.

- **Harness worker** (`app.harness_main`, Compose `pipeshub-ai`) — no HTTP port of its own. Claims runs from Node's Mongo run queue and drives the native loop or a coding engine; the engine is supervised from outside its sandbox.
- **Tool Gateway** `:8098` (`app.harness_main`, Compose `pipeshub-ai`, alias `tool-gateway`) — the per-run MCP endpoint at `/harness/tool-gateway`, inside the worker. Internal only.
- **Model Gateway** `:8096` (`app.model_gateway_main`, Compose `pipeshub-ai`, alias `model-gateway`) — Anthropic Messages, OpenAI Responses and Chat Completions for engines, with the org's keys and the run's budget. Starts with the worker unless `MODEL_GATEWAY_ENABLED` says otherwise. Internal only.
- **Harness state API** `:3002` (`src/modules/agent-harness/transport/internal-listener.ts`, Compose `pipeshub-ai`, alias `harness-state`) — Node's worker-only routes (journal, leases, gateway tokens) on an unpublished port, `AGENT_HARNESS_INTERNAL_PORT`. Public harness routes stay on `3000`: `/api/v1/conversations/{id}/runs…`, `/api/v1/agents/{key}/conversations/…` and `/api/v1/agent-harness/*`, all in `pipeshub-openapi.yaml`.
- **Sandbox broker** `:8095` (`app.sandbox_broker_main`, Compose `sandbox-broker`) — starts every sandbox and attaches the egress proxy to its network; callers need the worker credential. It holds no Docker socket: it reaches Docker only through the Docker socket proxy, with its own credential.
- **Egress proxy** `:8097` data, `:8098` control (`app.egress_proxy_main`, Compose `egress-proxy`, alias `egress-proxy-control`) — the only way out of a sandbox. Forwards only to the gateways and swaps each sandbox's placeholder for a run-bound token; the control port listens only on `sandbox_plane`. `docker-compose.egress-tunnels.yml` adds CONNECT tunnels (the Claude sign-in add-on).
- **Docker socket proxy** `:2375` (`app.docker_proxy_main`, Compose `docker-socket-proxy`) — the only container with the Docker socket, on `sandbox_docker` with the broker alone. Each caller's credential picks its profile: the broker's (`app/sandbox/docker_proxy_broker.py`, this install's sandboxes only, nothing of the host) and, while an install still runs `SANDBOX_MODE=docker`, the app's legacy one (`docker-compose.sandbox-legacy.yml`).

## Where the UI listens

These two local setups look similar and are not the same. Use the origin you actually opened in the browser; MCP is always `{that origin}/mcp`.

**Docker Compose / `install.sh` (default local run).** The all-in-one container listens on **3000** inside the container. Express serves `/api`, `/mcp`, and the built Next.js SPA from `public/` (`backend/nodejs/apps/src/app.ts`). Compose maps `${APP_PORT:-3000}:3000`, so the dashboard, REST API, and MCP endpoint are all `http://localhost:3000` unless the installer picked another `APP_PORT`. Open the UI there. There is no separate dashboard process on 3001 in this path.

**From-source contributor split (`CONTRIBUTING.md`).** Express still defaults to **3000** (`process.env.PORT || '3000'` in `app.ts`). The Next.js *dev* server is started on **3001** (`PORT=3001 npm run dev`) so it does not collide with Express. That is why `ALLOWED_ORIGINS` and `FRONTEND_PUBLIC_URL` in `backend/env.template` are `http://localhost:3001`, and why Playwright's `BASE_URL` is 3001. `npm start` in `frontend/` (`next start -p 3001`) is the same split in production-mode Next, not Docker.

**Helm.** Charts typically publish the combined Node service (UI + API in one process) on **3001**. That is a chart convention, not the Docker Compose default.

## Build and test

Python 3.12, Node 24 LTS (22 is the minimum), Docker. Full setup, including creating the venv, is in [CONTRIBUTING.md](./CONTRIBUTING.md) (around the `python3.12 -m venv venv` step). Do not invent a venv path here.

```bash
cd backend/python && source venv/bin/activate && pytest
cd backend/nodejs/apps && npm test
```

Run one test while iterating, then the suite before you finish:

```bash
cd backend/python && pytest tests/unit/path/test_x.py::test_name -x   # pytest.ini adds -q --timeout=30
cd backend/nodejs/apps && npx cross-env TS_NODE_PROJECT=tsconfig.test.json mocha --grep 'describe or it text'
cd frontend && npx vitest run path/to/file.test.ts
```

Python tests marked `integration` or `docker` need running services; unit tests do not.

Python suites also need Node 22 (`.nvmrc`) on `PATH` and `npm ci` in `backend/nodejs/apps`: the C-5 codegen check,
the kernel/journal parity tests and the evals call into the TypeScript side. Without Node they skip locally;
CI sets `PXH_REQUIRE_NODE=1`, so the C-5 skip fails there. Install the Python side from the hashed lock with
`requirements/install.sh dev --project . --editable` (in `backend/python`), as CI does. Changing a dependency means
regenerating the locks: [docs/agent-harness-v2/guides/SUPPLY-CHAIN.md](./docs/agent-harness-v2/guides/SUPPLY-CHAIN.md).

To check the whole tree at once, `scripts/verify.sh` runs every suite that needs
no Docker, network or running instance — Python, Node, frontend, Electron and
the shell suites — and prints one summary. `scripts/verify.sh --list` shows what
would run and why anything is skipped.

Style: [.gemini/styleguide.md](./.gemini/styleguide.md) (Ruff, PEP 8, ESLint, no secrets).

## Logging and errors

Read [docs/logging.md](./docs/logging.md) before you add a log line or handle an error. The rules that apply everywhere:

- **The message is static text or a dotted event name** (`sync_run.end`). Ids and values go in snake_case fields, named the same in Python and Node. Fields are scalars: ids, counts, enums.
- **Never log** secrets, tokens, request or response bodies, prompts, document content, emails or names. Driver, provider and model error text counts as content: in Python use `error_class.log_failure(..., include_error_text=False)` for it, and `failure_reason(exc)`, never `str(e)`, in anything a client, tool result or trace receives. CI checks for emails and names in log calls (`log-pii`).
- **Log an error once, where it is handled.** Python: `error_class.log_failure(logger, event, exc, **ids)`; lower layers `raise … from e`. Node: pass it to `next(err)` and the error middleware writes the line.
- **Bind ids once where work enters** (a request, a broker message, a tick). `loop.run_in_executor`, `ThreadPoolExecutor.submit` and `threading.Thread` do not copy the context; run the work in `contextvars.copy_context().run`.
- **An outage is not an absence.** A store, graph or config read that failed raises (`GraphQueryError`, `get_config(..., raise_on_error=True)`, `PermissionVerificationUnavailableError`) and the route answers an error. It is never read as "not found", "no access", "not configured" or "nothing to delete".
- **A Python 4xx `detail` is shown to the user.** Node passes a 4xx's words through and replaces a 5xx with its own (`libs/errors/backend-error.ts`). So a 4xx `detail` holds no exception text, raw path value or email.
- **Python's KV config has no compare-and-set.** `create_config_if_absent` is its only conditional write, so a read-modify-write there can lose a concurrent write. Node has `KeyValueStoreService.compareAndSet`; use it to rewrite a stored list or object. It is atomic on Redis and PostgreSQL only: on etcd (`Etcd3DistributedKeyValueStore`) it reads, compares and writes in separate calls, so two writers can both succeed there.

## Conventions

- Python config reads and writes go through `ConfigurationService` (`config_service.get_config(key)`, `set_config(key, value)`), never `KeyValueStore` directly. Use `get_config(key, use_cache=False)` when you need the value right after an update. Caching and failure rules are in [backend/python/app/config/AGENTS.md](./backend/python/app/config/AGENTS.md).
- `app/edition*.py`, `src/config.ts` and `frontend/config.ts` are extension points: keep the OSS default working and don't add edition-specific logic elsewhere ([CONTRIBUTING.md, Extension points](./CONTRIBUTING.md#extension-points)).
- Comments explain only the why the code cannot show: a non-obvious constraint or the bug a line guards against. No comments that restate the code or narrate the change, no commented-out code, no TODOs without an owner.
- PR review criteria: [.claude/skills/pr-review/SKILL.md](./.claude/skills/pr-review/SKILL.md). Frontend work: [frontend/AGENTS.md](./frontend/AGENTS.md) (Collections vs Knowledge Base naming, styling, i18n).

## Do not

- Commit secrets, tokens, or `.env` values.
- Use OAuth `client_credentials` for anything that must act as a user. PATs carry `userId` + `orgId`.
- Print or log personal access tokens. Newly minted PATs may have a `phpat_` prefix; strip happens in `extractToken`.
- Trust client-supplied org/user IDs — check auth on every new route and tool.
- Bypass factories (`ConnectorFactory`, `GraphDBProviderFactory`, `VectorDBProviderFactory`, `KeyValueStoreFactory`, `MessagingFactory`) or talk to the clients of PipesHub's own stores (Qdrant/Arango/Neo4j/PostgreSQL/etcd) from feature code. A connector's client for its own source is fine.
