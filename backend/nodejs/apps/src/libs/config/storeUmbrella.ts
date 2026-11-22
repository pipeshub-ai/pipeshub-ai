/**
 * `PIPESHUB_STORE=postgres`: one switch for the PostgreSQL runtime.
 *
 * For every setting that is not already set it fills in the per-store settings this runtime adds: the
 * key-value store, the message broker, the crawl scheduler and the shared state (`REDIS_MODE`). A setting
 * that is set always wins, so a mixed deployment (PostgreSQL configuration, Kafka messages) still works.
 * Unset, nothing changes. The Python services have the same switch in `app/utils/store_umbrella.py`; keep
 * the two lists identical.
 */
export const UMBRELLA_ENV = 'PIPESHUB_STORE';
export const POSTGRES_SETTINGS: Readonly<Record<string, string>> = {
  KV_STORE_TYPE: 'postgres',
  MESSAGE_BROKER: 'postgres',
  CRAWL_SCHEDULER: 'postgres',
  REDIS_MODE: 'postgres',
};

/** Call once, after `.env` is loaded and before anything reads the per-store settings. */
export function applyStoreUmbrella(
  env: NodeJS.ProcessEnv = process.env,
): boolean {
  const value = (env[UMBRELLA_ENV] ?? '').trim().toLowerCase();
  if (!value) return false;
  if (value !== 'postgres') {
    throw new Error(
      `${UMBRELLA_ENV}='${value}' is not supported; use 'postgres' or leave it unset`,
    );
  }
  for (const [key, setting] of Object.entries(POSTGRES_SETTINGS)) {
    if (!env[key]?.trim()) env[key] = setting;
  }
  return true;
}
