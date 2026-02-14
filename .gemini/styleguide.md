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
