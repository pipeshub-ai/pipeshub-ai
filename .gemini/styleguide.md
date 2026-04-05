# PipesHub Code Style Guide

## General Principles

- Keep functions focused and small. Each function should do one thing well.
- Prefer clarity over cleverness. Code is read far more often than it is written.
- Avoid dead code, unused imports, and commented-out blocks in production code.
- Handle errors explicitly. Do not silently swallow exceptions.
- Use meaningful variable and function names that convey intent.

## Python

- Follow PEP 8 conventions. Use Ruff for linting (line-length: 88, target: Python 3.10).
- Use type hints for function signatures.
- Use `async`/`await` consistently; do not mix sync and async patterns unnecessarily.
- Prefer f-strings for string formatting over `.format()` or `%` style.
- Use `logging` module with structured messages (`logger.info("msg: %s", val)`) instead of print statements.
- Avoid bare `except:` clauses. Catch specific exceptions.
- Use dependency injection via `dependency-injector` containers rather than direct instantiation of services.

## TypeScript / JavaScript

- Use TypeScript for all new code. Avoid `any` types where possible.
- Follow ESLint with airbnb-typescript configuration.
- Use Prettier for formatting (no semicolons in Node.js backend).
- Prefer `const` over `let`. Never use `var`.
- Use async/await over raw Promises and callbacks.
- Use BEM naming for CSS/SCSS class names.

## Security

- Never commit secrets, API keys, passwords, or tokens to the repository.
- Validate and sanitize all external inputs (user input, API responses, query parameters).
- Use parameterized queries for database operations. Never concatenate user input into queries.
- Do not disable SSL/TLS verification in production code.

## Configuration Access

- **CRITICAL: In Python services, never use `key_value_store` / `KeyValueStore` / `EncryptedKeyValueStore` directly for reading or writing configuration values.** Always use `configuration_service` / `ConfigurationService` instead. The `ConfigurationService` wraps the key-value store with LRU caching, environment variable fallbacks, and cross-process cache invalidation via Redis Pub/Sub. Bypassing it causes stale caches across services and breaks cache consistency. The only place that should instantiate or interact with `KeyValueStore` directly is the `ConfigurationService` itself and the DI container wiring.
- When reading config that must reflect the latest value (e.g., after an update), use `config_service.get_config(key, use_cache=False)`.
- When writing config, use `config_service.set_config(key, value)` which handles encryption, caching, and cache invalidation publishing automatically.

## Error Handling

- Log errors with sufficient context (key identifiers, operation being performed).
- Do not catch exceptions just to re-raise them without adding context.
- In API endpoints, return appropriate HTTP status codes with descriptive error messages.
- Use structured error responses consistently across all endpoints.

## Testing

- Write tests for new functionality and bug fixes.
- Do not commit code that breaks existing tests.
- Mock external dependencies (databases, APIs, message queues) in unit tests.

## OpenAPI Specification (BLOCKER)

The project maintains a single OpenAPI spec at `backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml`. This spec MUST stay in sync with all API changes. **Treat any mismatch as a blocking issue on the PR.**

### How to detect API changes in the PR diff

Scan the **entire PR diff** (all changed files, not just specific paths) for any of the following code patterns. These indicate that the PR introduces, modifies, or removes API surface area:

**Express / Node.js patterns (TypeScript/JavaScript):**
- `router.get(`, `router.post(`, `router.put(`, `router.patch(`, `router.delete(`, `router.options(`, `router.head(`
- `app.get(`, `app.post(`, `app.put(`, `app.patch(`, `app.delete(`
- `Router()` — new router instantiation
- Route path strings like `'/api/...'`, `'/v1/...'`

**FastAPI / Python patterns:**
- `@router.get(`, `@router.post(`, `@router.put(`, `@router.patch(`, `@router.delete(`
- `@app.get(`, `@app.post(`, `@app.put(`, `@app.patch(`, `@app.delete(`
- `APIRouter(` — new router instantiation
- Pydantic `BaseModel` subclasses used as request/response types in endpoint signatures
- Changes to `status_code=`, `response_model=`, `dependencies=` in route decorators

**General patterns (any language/framework):**
- New or changed HTTP method handlers
- Changes to URL path definitions or route prefixes
- Added, removed, or renamed query/path/header parameters
- Changes to request body validation or response schemas
- Changes to authentication/authorization decorators or middleware applied to routes (e.g., `@authenticate`, `authMiddleware`, `Depends(...)`)
- Changes to response status codes or error response shapes

### When OpenAPI updates are required

If ANY of the above patterns appear in the PR diff as **added or modified lines**, the PR MUST include corresponding updates to `pipeshub-openapi.yaml`. Specifically:

1. **New endpoints**: Any new route handler added anywhere in the codebase.
2. **Removed endpoints**: Any route handler deleted or commented out.
3. **Changed HTTP methods or paths**: Method or URL path of an existing endpoint is modified.
4. **Request body changes**: Fields added, removed, or modified in request body schemas/validators.
5. **Response schema changes**: Fields, status codes, or content types changed in responses.
6. **Query/path/header parameter changes**: Parameters added, removed, renamed, or changed in type/required status.
7. **Authentication/authorization changes**: Auth requirements added or removed from endpoints.
8. **New or modified middleware**: Middleware that changes request/response shape applied to routes.

### Review instructions

- Scan **every file in the PR diff** for the code patterns listed above. Do NOT limit your search to specific directories or file naming conventions — routes can be defined in any file.
- If the PR contains any of these patterns in added/modified lines but does NOT include changes to `backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml`, flag this as a **BLOCKER**.
- Use this exact comment format so it is clearly visible:

  > **🚫 BLOCKER: OpenAPI spec update required**
  >
  > This PR modifies API endpoints/contracts but does not update the OpenAPI specification at `backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml`.
  >
  > The OpenAPI spec must stay in sync with all API changes. Please update it to reflect the changes introduced in this PR before merging.
  >
  > **Detected API changes in:**
  > _(list every file and the specific route pattern detected, e.g., "`src/modules/auth/routes/userAccount.routes.ts` — added `router.post('/verify-email', ...)`")_

- If the PR DOES update the OpenAPI spec alongside API changes, verify that the spec changes accurately reflect the code changes (correct paths, methods, parameters, schemas, and status codes).
- If the PR only modifies internal logic with no route/contract changes (e.g., fixing a bug inside a handler without changing its signature, inputs, or outputs), no OpenAPI update is needed — do not flag it.
