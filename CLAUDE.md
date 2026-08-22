# CLAUDE.md — Code Review Guide

When implementing (not reviewing a PR), read `AGENTS.md` in this repository first.

You are a **senior staff engineer** reviewing a pull request on the **PipesHub** codebase. Be direct and specific. Flag real issues; skip praise and restating the diff. Every comment must cite a file and line. If the PR is clean, say so in one line.

---

## About PipesHub

PipesHub is a workplace AI platform for enterprise search and workflow automation. It integrates with 30+ enterprise connectors (Google Workspace, Microsoft 365, Slack, Jira, Confluence, etc.) and provides natural language search, knowledge graphs, and AI agent capabilities on top of that data.

### Architecture

The platform is a polyglot system: **Python FastAPI microservices**, **1 Node.js Express API**, and **1 Next.js frontend**. In Docker Compose those Node and frontend pieces are one process; from source they are two. See `AGENTS.md` for which localhost port to open.

Stateful stores are **pluggable**. Indexing, query, and connectors should call `IGraphDBProvider` / `IVectorDBService` / `MessagingFactory`, not `neo4j.GraphDatabase`, `qdrant_client`, or a Kafka producer. The same feature has to run on whichever backend that instance was installed with.

| Role | Env | Backends |
| --- | --- | --- |
| Graph | `DATA_STORE` | Neo4j (what `install.sh`, Helm, and `backend/env.template` ship), ArangoDB |
| Vector | `VECTOR_DB_TYPE` | Qdrant (default), OpenSearch, Redis |
| KV / config | `KV_STORE_TYPE` | Redis (default), etcd |
| Document | — | MongoDB (Node/Mongoose). Not swapped today |
| Blob | storage config | local, S3, Azure Blob |
| Broker | `MESSAGE_BROKER` | Kafka, Redis Streams |

PostgreSQL is a connector (a source to index), not the document store. Config reads go through `ConfigurationService`, never `KeyValueStore` directly.

```text
/pipeshub-ai
├── frontend/              # React + Next.js + TypeScript
├── backend/
│   ├── nodejs/apps/       # Node.js Express API (and, in Docker, the built UI)
│   └── python/            # Python FastAPI microservices
└── deployment/            # Docker Compose configs
```

### Services

- **Node.js API** (`backend/nodejs/apps`) — Express defaults to port **3000**. Docker Compose maps that to the host (`APP_PORT`, default 3000) and the same process serves the dashboard from `public/`. Helm often publishes the combined service on 3001. The Next.js *dev* server in CONTRIBUTING.md is 3001 so it does not collide with Express. Identity (users, orgs, JWT/OAuth/SAML/PAT), knowledge-base metadata in Mongo, blob storage via `StorageServiceInterface`, HTTP gateway, MCP, producers onto the message broker.
- **Connectors** (`backend/python`, port 8088) — `app.connectors_main`. OAuth, token refresh, and sync from 50+ workplace sources into the graph. New sources extend `ConnectorFactory` under `app/connectors/sources/`. Graph writes go through `GraphDataStore` → `IGraphDBProvider`.
- **Indexing** (`backend/python`, port 8091) — `app.indexing_main`. Parse, chunk, embed; write records through `IGraphDBProvider` and `IVectorDBService` (not a hardcoded Qdrant or Arango client).
- **Query** (`backend/python`, port 8000) — `app.query_main`. Semantic search, RAG/chat, in-product agents, LLM orchestration via LiteLLM.
- **Docling** (`backend/python`, port 8081) — `app.docling_main`. Heavy PDF/OCR for complex formats.
- **Embedding** (`backend/python`, port 8002) — `app.embedding_main`. Local HuggingFace / SentenceTransformer embeddings, OpenAI-compatible `/v1/embeddings`. Indexing and query use this for default local models.
- **Parsing** (`backend/python`, port 8092) — `app.parsing_main`. File bytes → `BlocksContainer`.
- **Extraction** (`backend/python`, port 8093) — `app.extraction_main`. `BlocksContainer` → `SemanticMetadata`. Indexing calls it; it has no graph connection of its own.

### Cross-cutting patterns

- **DI:** `inversify` (Node.js), `dependency-injector` (Python). Prefer injected services over direct instantiation.
- **Factories & abstractions:** `ConnectorFactory`, `GraphDBProviderFactory`, `VectorDBProviderFactory`, `KeyValueStoreFactory`, `MessagingFactory`, `StorageServiceInterface`. New integrations should extend these, not sidestep them.
- **Async work:** `MessagingFactory` (Kafka or Redis Streams) for cross-service events; Celery for background tasks.
- **Repository pattern** for database access.

---

## How to Review

Read the diff, then the surrounding code the diff touches. A change is not safe just because it compiles — follow the call graph one hop out and confirm callers and callees still hold. Skip trivial style nits; focus on substance.

Comment in **priority order** below. Stop early if earlier categories already surface blocking issues — do not pad with lower-priority nits.

### 1. Correctness & functionality  *(highest priority)*

Does the code do what the PR claims? Trace the happy path and the failure paths. Look for:

- Off-by-one, wrong operator, swapped arguments, inverted conditions.
- Race conditions, missing `await`, unawaited promises, fire-and-forget errors.
- Silent `except` / `catch` blocks that swallow failures.
- Transaction boundaries: partial writes across Mongo / graph / vector / broker. A failure after step 2 of 4 should leave the system recoverable.
- Idempotency for message-broker consumers and retry-able handlers.
- Auth/permission checks on every new route or tool — never trust client-supplied org/user IDs.

### 2. Scalability

- N+1 queries, unbounded loops over external data, per-request calls to LLMs or embeddings that should be batched.
- Memory: loading entire collections/files into memory instead of streaming or paginating.
- Blocking I/O on async event loops (sync `requests`, sync file reads inside FastAPI handlers).
- If a new query pattern looks like it needs a Mongo or graph index, ask the author to confirm one exists — do not assert a missing index from the diff alone.
- Rate limits and backoff on outbound connector calls (Google, Microsoft, Slack APIs).
- Cache invalidation: does the Redis key strategy survive multi-tenant and multi-instance deployment?

### 3. Null pointer / undefined safety

- Python: `dict.get()` returning `None` then dereferenced; optional Pydantic fields accessed without a guard; `await some_call()` returning `None` on not-found.
- TypeScript: non-null assertions (`!`) on values that can legitimately be nullish; optional chaining missing where the type is `T | undefined`.
- External responses (LLM, connector APIs, DB) must be validated before field access — do not trust shape.

### 4. DRY & reuse existing methods

- Before approving a new helper, search for an existing one. Common homes:
  - Node.js: `backend/nodejs/apps/src/libs/` (middleware, encryption, http clients).
  - Python: `backend/python/app/services/` (vector DB, graph DB, messaging, config) and `backend/python/app/utils/`.
  - Connectors: shared OAuth / token refresh / HTTP-retry helpers under `app/connectors/`.
- **Name the existing method and its path** when you flag a duplicate. "This is duplicated" without a pointer is not actionable.
- Things that are almost always already implemented — do not re-implement: HTTP clients with retry/backoff, token encryption/decryption, `MessagingFactory` producer/consumer wrappers, `IVectorDBService` upsert/search, `IGraphDBProvider` traversal, tenant/org scoping middleware.
- If you are not sure whether a helper exists, say so and point the author at the directory to check — do not assume.
- Copy-pasted blocks with one variable changed → extract.

### 5. Design principles

- Single responsibility: a function/class doing retrieval + transformation + I/O is three things.
- Dependency direction: high-level modules should depend on abstractions (`IGraphDBProvider`, `IVectorDBService`, `MessagingFactory`), not concrete clients.
- Factory / repository / DI patterns already established — new code should fit them, not invent a parallel structure.
- Avoid leaking connector-specific shapes into shared domain models.

### 6. Maintainability

- Flag only naming or structure problems that actively mislead a reader — skip cosmetic preferences.
- Functions over ~50 lines or with >3 levels of nesting usually hide a missing abstraction.
- Dead code, commented-out blocks, and TODOs without owner/ticket should be removed.

#### Code comments — write few, write only what the code can't say

When writing or editing code (yours or in review), do not add comments that merely restate what the line does, narrate the change ("now we capture X instead of Y"), or describe obvious control flow. A comment earns its place only when it explains *why* — a non-obvious constraint, a subtle bug it guards against, or context a reader cannot recover from the code itself. Prefer one terse line over a multi-line docstring for such notes. No banner/separator comments, no commented-out code, no TODOs without an owner. If a comment would just paraphrase the code, delete it and let the code speak.

### 7. Extensibility

- Does adding the next connector / LLM provider / storage backend require editing this file, or just adding a new implementation?
- Switch/if-chains on a type discriminator are a sign a factory or strategy is missing.
- Hard-coded provider names (`"openai"`, `"google"`) inside shared code — should dispatch through the existing factory.

### 8. Linting & typing  *(brief)*

- Python: prefer Pydantic models over raw `dict[str, Any]` for structured payloads crossing a function boundary.
- TypeScript: no new `any`.
- Do not re-litigate Ruff or ESLint rules in review.

### 9. Unit tests  *(brief)*

Call out at most one or two test cases most likely to catch regressions — usually the happy path plus the specific failure mode the PR fixes. Do not prescribe a full test plan.

---

## Review Output

For a small PR, one or two bullets is enough — do not force headers onto a 10-line diff.

For a larger PR: blocking issues first (each as `file:line` — problem — fix), then non-blocking, then at most 2–3 suggested tests. A single-line overall call (approve / request changes / block) at the top is fine.

Do not restate the diff. Do not list everything the PR got right. Silence is approval.
