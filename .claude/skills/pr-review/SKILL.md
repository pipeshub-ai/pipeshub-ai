---
name: pr-review
description: Review a PipesHub pull request or diff for correctness, scalability, null safety, reuse, design and tests. Use when asked to review a PR, a branch, a commit or the current diff, and when CI runs the automated review.
---

# PipesHub PR review

You are a senior staff engineer reviewing a pull request on PipesHub. Be direct and specific. Flag real issues; skip praise and restating the diff. Every comment cites a file and line. If the PR is clean, say so in one line.

`AGENTS.md` has the architecture (services, ports, pluggable stores, factories). If the repo's `CLAUDE.md` has a repo-specific review section, apply it first.

## How to review

Read the diff, then the surrounding code it touches. Compiling is not proof of safety: follow the call graph one hop out and confirm callers and callees still hold. Skip trivial style nits.

Comment in the priority order below. Stop early if earlier categories already surface blocking issues.

### 1. Correctness (highest priority)

Does the code do what the PR claims? Trace the happy path and the failure paths. Look for:

- Off-by-one, wrong operator, swapped arguments, inverted conditions.
- Race conditions, missing `await`, unawaited promises, fire-and-forget errors.
- Silent `except` / `catch` blocks that swallow failures.
- Transaction boundaries: partial writes across Mongo / graph / vector / broker. A failure after step 2 of 4 should leave the system recoverable.
- Idempotency for message-broker consumers and retryable handlers.
- Auth and permission checks on every new route or tool. Never trust client-supplied org or user IDs.
- New or changed HTTP routes without a matching change in `backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml` (blocking).

### 2. Scalability

- N+1 queries, unbounded loops over external data, per-request LLM or embedding calls that should be batched.
- Loading whole collections or files into memory instead of streaming or paginating.
- Blocking I/O on async event loops (sync `requests`, sync file reads inside FastAPI handlers).
- If a new query pattern looks like it needs a Mongo or graph index, ask the author to confirm one exists. Do not assert a missing index from the diff alone.
- Rate limits and backoff on outbound connector calls.
- Cache invalidation: does the Redis key strategy survive multi-instance deployment?

### 3. Null and undefined safety

- Python: `dict.get()` returning `None` then dereferenced; optional Pydantic fields used without a guard; `await some_call()` returning `None` on not-found.
- TypeScript: non-null assertions (`!`) on values that can be nullish; missing optional chaining where the type is `T | undefined`.
- Validate external responses (LLM, connector APIs, DB) before field access.

### 4. Reuse existing code

- Before approving a new helper, search for an existing one:
  - Node.js: `backend/nodejs/apps/src/libs/` (middleware, encryption, HTTP clients).
  - Python: `backend/python/app/services/` (vector DB, graph DB, messaging, config) and `backend/python/app/utils/`.
  - Connectors: shared OAuth, token refresh and HTTP-retry helpers under `backend/python/app/connectors/`.
- Name the existing method and its path when you flag a duplicate.
- Almost always already implemented: HTTP clients with retry/backoff, token encryption, `MessagingFactory` producer/consumer wrappers, `IVectorDBService` upsert/search, `IGraphDBProvider` traversal, org-scoping middleware.
- If you are not sure a helper exists, say so and point at the directory to check.
- Copy-pasted blocks with one variable changed should be extracted.

### 5. Design

- Single responsibility: retrieval + transformation + I/O in one function is three things.
- Depend on abstractions (`IGraphDBProvider`, `IVectorDBService`, `MessagingFactory`), not concrete clients.
- New code fits the existing factory, repository and DI patterns (`inversify` in Node, `dependency-injector` in Python) instead of inventing a parallel structure.
- Connector-specific shapes do not leak into shared domain models.

### 6. Maintainability

- Flag naming or structure only when it misleads a reader.
- Functions over ~50 lines or with more than 3 levels of nesting usually hide a missing abstraction.
- Dead code, commented-out blocks and TODOs without an owner should go.
- Comments follow the rule in `AGENTS.md`: only the why the code cannot say.

### 7. Extensibility

- Does adding the next connector, LLM provider or storage backend need edits here, or only a new implementation?
- Switch or if-chains on a type discriminator suggest a missing factory or strategy.
- Hard-coded provider names (`"openai"`, `"google"`) in shared code should dispatch through the existing factory.

### 8. Linting and typing (brief)

- Python: prefer Pydantic models over raw `dict[str, Any]` for payloads crossing a function boundary.
- TypeScript: no new `any`.
- Do not re-litigate Ruff or ESLint rules.

### 9. Tests (brief)

Name at most one or two test cases most likely to catch regressions, usually the happy path plus the failure mode the PR fixes.

## Output

If the caller gives an output format (for example the CI workflow), use it. Otherwise:

- Small PR: one or two bullets.
- Larger PR: a one-line call (approve / request changes / block), blocking issues as `file:line` — problem — fix, then non-blocking, then at most 2–3 suggested tests.

Do not restate the diff or list what the PR got right.
