/**
 * Edition constants that must stay import-free.
 *
 * This is the only edition switch that `libs/services/telemetry` may import:
 * the metrics backend is chosen at module load, and `config.ts` re-exports
 * routes that import telemetry, so importing from there would cycle.
 */

// Used when TELEMETRY_COLLECTION_FLAG is unset.
export const TELEMETRY_DEFAULT_ENABLED = true;

/**
 * Whether the admin routes that drop and rebuild the shared records vector
 * collection are offered. A deployment where one collection holds more than
 * one workspace's records turns this off, and the routes then answer 403.
 */
export const VECTOR_STORE_REBUILD_AVAILABLE = true;

/** Sign-in methods this edition does not offer; saved policies that name them are rewritten. */
export const REMOVED_SIGN_IN_METHODS: readonly string[] = ['samlSso', 'oauth'];

/** Sign-in methods this edition offers under another name. */
export const RENAMED_SIGN_IN_METHODS: Readonly<Record<string, string>> = {
  azureAd: 'microsoft',
};

/**
 * Logging provider override, or `null` for the default text logging. Typed
 * `unknown` so this file stays import-free; `getProvider` in
 * `libs/services/structured-logging` checks the shape.
 */
export function structuredLoggingProvider(): unknown {
  return null;
}

/**
 * Keys inside an AI model's `configuration`, besides the built-in public ones,
 * that hold settings rather than secrets, so reads return them as stored.
 */
export const EXTRA_AI_PUBLIC_CONFIG_KEYS: readonly string[] = [];
