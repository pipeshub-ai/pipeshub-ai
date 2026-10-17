/**
 * Edition constants that must stay import-free.
 *
 * This is the only edition switch that `libs/services/telemetry` may import:
 * the metrics backend is chosen at module load, and `config.ts` re-exports
 * routes that import telemetry, so importing from there would cycle.
 */

// Used when TELEMETRY_COLLECTION_FLAG is unset.
export const TELEMETRY_DEFAULT_ENABLED = true;

/** Sign-in methods this edition does not offer; saved policies that name them are rewritten. */
export const REMOVED_SIGN_IN_METHODS: readonly string[] = ['samlSso', 'oauth'];

/** Sign-in methods this edition offers under another name. */
export const RENAMED_SIGN_IN_METHODS: Readonly<Record<string, string>> = {
  azureAd: 'microsoft',
};
