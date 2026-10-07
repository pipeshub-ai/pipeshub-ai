/**
 * Edition switch for Redis connection providers.
 *
 * The factory registers the OSS modes (standalone, cluster) itself; any
 * other REDIS_MODE must be registered once per *process* before the first
 * `getRedisProvider()` call. Every Node entry point in this package -- the
 * API (`src/app.ts`) and each standalone integration
 * (`src/integrations/<name>/src/index.ts`) -- imports this module for that,
 * and `tests/libs/services/redis/architectureGuard.test.ts` enforces it.
 * The all-in-one Docker image runs those entry points as separate processes
 * from one .env, so a registration made in the API does not exist in the
 * Slack bot; importing this module everywhere is what keeps the set identical.
 *
 * OSS registers nothing here. An EE checkout uncomments its provider
 * imports below. `REDIS_PROVIDER_MODULE` remains the hook for a provider
 * that lives outside this source tree.
 */

// EE
// import './ee/libs/services/redis/memorydbProvider';

export {};
