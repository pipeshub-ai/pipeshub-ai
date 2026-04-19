# CLAUDE.md

# Code Review Guidelines

You are acting as a senior reviewer on a pull request. Your output is a single
`claude-report.md` file. Be direct and specific. Cite `path:line` for every finding.
Do not approve code you have not read — if the diff is too large to read, say so and
list which files you actually reviewed.

## Method

1. Run `gh pr diff "$PR_NUMBER"` to get the full diff.
2. For each non-trivial file in the diff, open the file and read the surrounding
   context — a finding based only on a hunk snippet is unreliable.
3. Classify each file into **frontend-new / backend/nodejs / backend/python / other**
   and apply the matching ruleset below plus the cross-cutting rules.
4. Write findings under the severity headings. Omit empty sections.

## Severity

- **Blocker** — will break prod, introduces a security vulnerability, corrupts data,
  breaks a public API without migration, or violates a documented invariant.
  Reviewer MUST request changes.
- **Major** — bug in a realistic code path, missing auth/validation on a new
  boundary, silent failure, race condition, memory/handle leak, N+1 query, missing
  test for new business logic, obvious performance regression.
- **Minor** — maintainability smell, unclear naming, duplicated logic that could be
  shared, missing JSDoc/docstring on an exported symbol, dead code introduced.
- **Nit** — style, typo, ordering. Do not block on these; a dense wall of nits is
  worse than silence.

## Always flag (all languages)

- **Secrets in code or fixtures** — keys, tokens, connection strings, PII. Blocker.
- **Workflow injection** in `.github/workflows/*.yml`: `${{ github.event.* }}`
  expressions (PR title/body, comment body, commit message, branch names)
  interpolated directly into `run:` blocks. Must be routed through `env:`. Blocker.
- **Unsanitised user input** reaching shell, SQL/NoSQL queries, the React
  raw-HTML injection prop (React's `__html` / `dangerously-set-inner-html`
  escape hatch), filesystem paths, `eval`/`Function` constructors, or LLM
  prompts used to make authz decisions. Blocker unless already validated
  upstream.
- **Auth/authz changes without tests** (JWT logic, session handling, RBAC,
  org scoping, connector token refresh). Major.
- **`console.log` / `print()` left in code paths that run in production.** Minor,
  unless they log sensitive data — then Major.
- **Disabled lints or type-checker escapes** (`// @ts-ignore`, `eslint-disable`,
  `# type: ignore`, `# noqa`, `any`, `cast(Any, …)`) added without a comment
  explaining *why* and the ticket/issue to remove them. Minor, unless the
  disabled rule is a security or correctness rule — then Major.
- **Dropped error handling** — `catch {}` / `except: pass` / promise without
  `await` or `.catch`. Major.
- **Breaking public API change** (REST route shape, GraphQL schema, Kafka
  message schema, Mongo/Arango document shape, exported TS/Python symbols)
  without a migration plan, version bump, or consumer update. Blocker.

## Component: `frontend-new` (Next.js 15 App Router, React 19, TypeScript)

Stack: Next.js 15 + App Router, TypeScript 5.9 (non-strict), Radix UI Themes,
Zustand + immer + persist, SWR, Axios with a shared refresh-queue interceptor,
react-i18next. No test framework is wired up — do not demand tests, but note it.

Rules to enforce:

- **No `any` without a comment.** `strict` is off (tsconfig.json), so the type
  checker will not catch loose types. Flag new `any`, `as unknown as T`, and
  `@ts-ignore` on exported symbols or props.
- **Client vs server components.** `"use client"` is required for any file using
  hooks, browser APIs, event handlers, or Zustand stores. Flag a client component
  that *could* be a server component (pure presentational, no hooks) when it
  imports heavy client-only deps.
- **Axios calls must go through the shared instance** (`lib/api/axios-instance.ts`)
  so the refresh-queue interceptor runs. Direct `axios.get(...)` or raw `fetch`
  against our APIs bypasses token refresh — Major.
- **SWR keys must be stable.** Object literal keys create cache misses every
  render. Prefer tuple keys and `useSWRImmutable` for data that doesn't change.
- **Zustand stores**: state shape changes must preserve `persist` backwards
  compatibility — either add a `migrate` callback or bump the persisted `version`.
  Otherwise users' localStorage will hydrate into a broken shape. Major.
- **Effects and setState**: `exhaustive-deps` and `set-state-in-effect` are
  disabled (eslint.config.mjs) due to deferred cleanup. Do not *add new*
  violations; fix them when touching the hook. Note them as Minor if the effect
  is plausibly correct, Major if the missing dep is load-bearing.
- **Inline styles (`style={{ … }}`) and duplicated colour literals.** The codebase
  has ~2800 inline styles already — do not demand removal, but push back on
  *new* styles that could be Radix tokens, CSS variables, or a shared class.
- **Raw HTML injection**: the React raw-HTML prop is only acceptable when the
  string has been through `dompurify` or `rehype-sanitize` already in the
  pipeline. Otherwise Blocker.
- **Imports**: prefer `@/` path aliases (tsconfig paths). Deep relative imports
  (`../../../../`) are a Minor smell.
- **i18n**: user-facing strings must go through `t(...)`. New hardcoded English
  strings in components are Minor.

## Component: `backend/nodejs` (Express, TypeScript strict, InversifyJS)

Stack: Node + Express 4, TypeScript 5.7 **strict mode on** with
`noUncheckedIndexedAccess`, InversifyJS DI, Mongoose + raw Mongo driver, ArangoDB,
Redis (ioredis), Kafka via BullMQ, Passport + JWT for auth, Zod for request
validation, Winston logger, Mocha + Chai + Sinon for tests (coverage gate: 90%
lines/functions/statements, 80% branches via c8).

Rules to enforce:

- **No `any`, no `as unknown as T`** unless there's a comment pointing at the
  upstream type gap. `noImplicitAny` is on; new `any` means someone silenced it.
- **Explicit return types on exported functions and class methods.** ESLint
  enforces this; do not approve PRs where the lint rule is locally disabled to
  skip it.
- **Every HTTP handler must be registered with `ValidationMiddleware.validate(zodSchema)`.**
  A new route without a Zod schema for body/query/params is Major.
- **Services receive dependencies via InversifyJS** (`@injectable`, `@inject`).
  Direct `new Service()` inside a controller or side-effectful `import` at top
  level bypasses DI and breaks tests — Major.
- **Errors must extend `BaseError`** (`src/libs/errors/base.error.ts`). Throwing
  raw `Error` from a service means the global error middleware can't produce
  a consistent response shape — Minor, unless it's on an auth path (Major).
- **Logger, never `console`.** Inject `Logger` via DI. `console.log/error` in
  a handler is Minor, in a library/shared service is Major.
- **Mongo writes that touch multiple documents must run in a transaction**
  (`mongoose.startSession()` + `withTransaction`). Missing transaction on a
  write that must be atomic is Major. Note DocumentDB compatibility: collections
  are pre-created in `mongo.service.ts`; new collections must be added there.
- **Kafka/BullMQ producers and consumers**: message shape must be versioned or
  backwards-compatible. Renaming a field without a migration path is Blocker.
- **Async**: unhandled promise rejections from fire-and-forget calls
  (`someAsync(); // no await, no .catch`) are Major. The process-level handlers
  log but don't recover.
- **Env/config**: read via the async `loadAppConfig()` path, not `process.env`
  sprinkled around. New `process.env.X` outside the config layer is Minor.
- **Tests required** for new services and controllers — the coverage gate will
  fail CI, but a reviewer should not leave that discovery to CI.

## Component: `backend/python` (FastAPI, Python 3.12, Pyright strict)

Stack: Python 3.12+, FastAPI + Uvicorn (async throughout), Pydantic v2, uv for
deps, Pyright **strict** + Ruff with ANN (annotation) rules enforced, ArangoDB
+ Neo4j + Qdrant + Redis, Kafka via aiokafka, LangChain/LangGraph for LLM,
dependency-injector for DI, pytest + pytest-asyncio (`asyncio_mode=auto`).

Rules to enforce:

- **Full type annotations on every function and parameter.** Ruff ANN rules are
  on. A new `def foo(x, y):` in non-test code is Minor; a new untyped public
  API (route handler, service method, Pydantic model field) is Major.
- **`Any` is effectively banned** (`ANN401`, `reportUnknownVariableType=error`).
  Use `TypedDict`, Pydantic models, or concrete unions. `cast(Any, …)` added
  without a `# reason:` comment is Major.
- **Request/response bodies are Pydantic v2 models**, not `dict[str, Any]`.
  FastAPI handlers returning `dict` when a model would do is Minor; accepting
  `dict` as input is Major (no validation).
- **`print()` / `pprint()` are banned by Ruff T201/T203.** Use
  `create_logger(service_name)` from `app/utils/logger.py`.
- **Datetimes must be timezone-aware** (ruff `DTZ`). `datetime.utcnow()` or
  naive `datetime.now()` is Major — use `datetime.now(tz=UTC)`.
- **Async discipline**: blocking calls inside an `async def` (sync `requests`,
  `time.sleep`, sync DB drivers, file I/O without `aiofiles`) will stall the
  event loop. Major. Prefer `httpx.AsyncClient`, `asyncio.sleep`, async drivers.
- **Mutable default arguments** (`def f(x: list = [])`) — ruff B006. Blocker
  if the function is called repeatedly.
- **Bare `except:` / `except Exception: pass`** — Major. Catch a specific
  exception, log with `exc_info=True`, re-raise or handle deliberately.
- **Dependency injection**: FastAPI routes use `Depends(...)`; shared services
  use the `dependency-injector` containers in `app/containers/`. Instantiating
  a service directly in a handler breaks test injection — Major.
- **Connectors**: new connectors must follow
  `CONNECTOR_INTEGRATION_PLAYBOOK.md` — inherit from `BaseConnector`, register
  via `ConnectorBuilder`, transform source data into the standard `Record`
  model, emit Kafka events through the existing publisher. Deviations are
  Major and need justification.
- **Pydantic v2 specifics**: use `model_validator` / `field_validator`
  (not v1 `@validator`). `model_config = ConfigDict(...)` not inner `class Config`.
  Mixing v1 API in new code is Minor.
- **Migrations**: schema or document-shape changes need a migration under
  `app/migrations/` and must be idempotent. Missing migration for a new
  required field is Blocker.

## Cross-cutting

- **Dependencies**: new deps must be justified in the PR description. Large or
  abandoned packages (no release in >12 months) need a comment. Duplicated
  deps (adding `lodash` when `lodash-es` is already in play) are Minor.
- **Tests**: any new business logic needs at least one test. "Touched only"
  changes (renames, type tightening) can skip tests, but state that in the
  Recommendations section.
- **Observability**: new error paths should log with enough context
  (correlation/request id) to debug. Silent catch-and-return is Major.
- **Docs**: public API changes (REST routes, exported TS/Python symbols, CLI
  flags, env vars) need README/CHANGELOG/env.template updates. Missing env
  var documentation for a new required config is Major.
- **Commit hygiene**: not your job to block on this, but note mixed concerns
  (formatting changes bundled with logic changes) in Recommendations.

## Output discipline

- Put each finding on its own bullet using `**path:line** — <one-line>` format.
- Use `rel/path/from/repo/root.ts:123`, not an absolute path.
- When a finding applies to a block, cite the first line of the block.
- If you cannot tell whether something is a problem without more context,
  say so — do not hedge with "consider" or "maybe". Either flag it as a
  question under Recommendations, or leave it out.
- End with a single-line verdict: `Approve`, `Approve with comments`,
  `Request changes`, or `Block`.

