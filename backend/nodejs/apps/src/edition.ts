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

/** Labs flags of this edition's own, read and saved beside the shared ones; a flag in neither list is dropped. */
export const EDITION_PLATFORM_FEATURE_FLAGS: readonly import('./modules/configuration_manager/constants/constants').PlatformFeatureFlagDef[] =
  [];

/** This edition's Labs flags the durable worker reads, pinned beside the shared WORKER_STAGES (rollout.<key>). */
export const EDITION_WORKER_STAGES: readonly string[] = [];

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

/**
 * Collections this edition stores besides this build's, for a move of the document store to PostgreSQL
 * (docstore-migrate, ADR-DOCMIG-01): every collection is copied, listed as not copied, or held, and preflight refuses
 * a move while a held one holds documents. `held` names the PostgreSQL tables its data lives in (none when the edition
 * keeps it in MongoDB only); `heldFields` are fields an edition adds to a collection the copy maps, held the same way.
 * `carriers` map what would otherwise stay held: each is a docstore-migrate `Carrier` (scripts/docstore-migrate/carry/types.ts)
 * that copies, follows, verifies, rolls back and resets its collections and fields, so a held collection or field it carries
 * (this build's or the edition's) is no longer held. Typed `unknown` so this file stays import-free; the migrator checks each
 * one's shape, and what it may carry, when it loads.
 */
export interface EditionDocstoreCoverage {
  held: ReadonlyArray<{
    collection: string;
    reason: string;
    tables: readonly string[];
  }>;
  ignored: ReadonlyArray<{
    collection: string;
    reason: string;
    tables?: readonly string[];
  }>;
  heldFields: ReadonlyArray<{
    collection: string;
    path: string;
    reason: string;
  }>;
  carriers: readonly unknown[];
}

export const EDITION_DOCSTORE_COVERAGE: EditionDocstoreCoverage = {
  held: [],
  ignored: [],
  heldFields: [],
  carriers: [],
};

/**
 * Throws to refuse, at start-up, a combination of settings this build does not
 * run. Each Node entry point calls it right after `applyStoreUmbrella()`, before
 * it opens a store or listens; it lives here, import-free, so the Slack bot can
 * call it without loading the API. OSS runs every combination.
 */
export function checkStartup(): void {
  return;
}
