/**
 * Every harness and control-plane store directory under `libs/repositories`, and whether it has a PostgreSQL
 * implementation (`<store>/pg.ts`). A test keeps this list equal to the directories and the flag equal to the files.
 *
 * Under `DOC_STORE=postgres` the harness runs only when no store is left on MongoDB: one missing store would leave a
 * run half in each database. Until then the harness reports itself unavailable with a plain message for the admin
 * (`postgres_incomplete`) and the rest of PipesHub serves as usual.
 */
import { docStore } from '../store-select';

export interface HarnessStoreEntry {
  /** `not-needed`: Mongo plumbing the PostgreSQL design has no counterpart for (the reason is beside it). */
  readonly postgres: boolean | 'not-needed';
}

export const HARNESS_STORES: Readonly<Record<string, HarnessStoreEntry>> = {
  'agent-harness/abandoned-forks': { postgres: true },
  'agent-harness/active-orgs': { postgres: true },
  'agent-harness/actor-authority': { postgres: true },
  'agent-harness/admission-quota-ledger': { postgres: true },
  'agent-harness/agent-erasures': { postgres: true },
  'agent-harness/approver-directory': { postgres: true },
  'agent-harness/attachment-carriers': { postgres: true },
  'agent-harness/audit': { postgres: true },
  'agent-harness/audit-anchor-sink': { postgres: true },
  'agent-harness/audit-linking': { postgres: true },
  'agent-harness/audit-names': { postgres: true },
  'agent-harness/budget-ledger': { postgres: true },
  'agent-harness/chat-session-repository': { postgres: true },
  'agent-harness/child-runs': { postgres: true },
  'agent-harness/coding-engine-preferences': { postgres: true },
  'agent-harness/control-plane-liveness': { postgres: true },
  'agent-harness/conversation-backfill-source': { postgres: true },
  'agent-harness/conversation-eraser': { postgres: true },
  'agent-harness/conversation-reader': { postgres: true },
  'agent-harness/conversation-retention': { postgres: true },
  'agent-harness/conversation-writer': { postgres: true },
  'agent-harness/data-protection': { postgres: true },
  'agent-harness/dispatch-outbox': { postgres: true },
  'agent-harness/durable-transaction': { postgres: true },
  'agent-harness/engine-runs-probe': { postgres: true },
  'agent-harness/engine-signin': { postgres: true },
  'agent-harness/erasure-backfill': { postgres: true },
  'agent-harness/execution': { postgres: true },
  'agent-harness/group-membership': { postgres: true },
  'agent-harness/history-source': { postgres: true },
  'agent-harness/inbox': { postgres: true },
  'agent-harness/journal': { postgres: true },
  'agent-harness/journal-durability-probe': { postgres: true },
  'agent-harness/journal-startup': { postgres: 'not-needed' }, // the replica-set and majority-write probe and the Mongo index migrations; on PostgreSQL the schema is Alembic's (`backend/db/versions/harness`) and every commit is durable
  'agent-harness/journal-tail': { postgres: true },
  'agent-harness/leader-lease': { postgres: true },
  'agent-harness/memory': { postgres: true },
  'agent-harness/migrations': { postgres: 'not-needed' }, // Mongo index migrations; on PostgreSQL the harness branch of `backend/db` owns tables and indexes
  'agent-harness/org-deleted-announcer': { postgres: true },
  'agent-harness/org-deletion': { postgres: true },
  'agent-harness/payload-store': { postgres: true },
  'agent-harness/pending-reaps': { postgres: true },
  'agent-harness/person-wait': { postgres: true },
  'agent-harness/principal-erasure': { postgres: true },
  'agent-harness/sandbox-snapshots': { postgres: true },
  'agent-harness/retention-store': { postgres: true },
  'agent-harness/run-admission-store': { postgres: true },
  'agent-harness/run-state': { postgres: true },
  'agent-harness/run-status': { postgres: true },
  'agent-harness/schedule-notifier': { postgres: true },
  'agent-harness/schedules': { postgres: true },
  'agent-harness/session-backup': { postgres: true },
  'agent-harness/session-erasure': { postgres: true },
  'agent-harness/session-fields': { postgres: true },
  'agent-harness/session-forks': { postgres: true },
  'agent-harness/session-roles': { postgres: true },
  'agent-harness/session-store-eraser': { postgres: true },
  'agent-harness/time': { postgres: true },
  'agent-harness/transaction-writes': { postgres: 'not-needed' }, // the batched writes of one Mongo commit; a PostgreSQL unit sends its statements on one connection
  'agent-harness/worker-compat-expr': { postgres: true },
  'agent-harness/worker-feature-sightings': { postgres: true },
  'agent-harness/workspace': { postgres: true },
  'agent-harness/workspace-head-cas': { postgres: true },
  'control-plane/bindings': { postgres: true },
  'control-plane/capability-ledger': { postgres: true },
  'control-plane/catalog': { postgres: true },
  'control-plane/change-outbox': { postgres: true },
  'control-plane/control-plane-transaction': { postgres: true },
  'control-plane/grant-store': { postgres: true },
  'control-plane/head-revisions': { postgres: true },
  'control-plane/installations': { postgres: true },
  'control-plane/migration-ledger': { postgres: true },
  'control-plane/migrations': { postgres: 'not-needed' }, // Mongo index migrations; the harness branch of `backend/db` owns the tables
  'control-plane/org-heads': { postgres: 'not-needed' }, // the ordered-steps writer's leases for standalone Mongo; PostgreSQL commits every write in one unit (`control-plane-transaction/pg.ts`), whose head row is `harness.cp_org_heads`
  'control-plane/policy': { postgres: true },
  'control-plane/user-directory': { postgres: true },
};

/** The stores that still have no PostgreSQL implementation, sorted. */
export function harnessStoresWithoutPostgres(
  stores: Readonly<Record<string, HarnessStoreEntry>> = HARNESS_STORES,
): string[] {
  return Object.entries(stores)
    .filter(([, entry]) => entry.postgres === false)
    .map(([name]) => name)
    .sort();
}

/** Whether every harness store can run on the selected document store. */
export const harnessStoresReady = (
  stores: Readonly<Record<string, HarnessStoreEntry>> = HARNESS_STORES,
): boolean =>
  docStore() !== 'postgres' ||
  harnessStoresWithoutPostgres(stores).length === 0;

/**
 * The store for the selected document store. Under `DOC_STORE=postgres` a store with no PostgreSQL implementation is
 * never handed out: its stand-in refuses every call, and the harness is unavailable (`harnessStoresReady`), so nothing
 * that admits or claims a run reaches it.
 */
export function pickHarnessStore<T extends object>(
  name: string,
  impls: { mongo: () => T; postgres?: () => T },
): T {
  if (docStore() !== 'postgres') return impls.mongo();
  if (impls.postgres) return impls.postgres();
  return unavailableStore<T>(name);
}

export class HarnessStoreUnavailableError extends Error {
  constructor(name: string) {
    super(
      `The agent harness is not available on this install: its ${name} store has no PostgreSQL implementation yet`,
    );
    this.name = 'HarnessStoreUnavailableError';
  }
}

// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- the caller names the store interface the proxy stands in for
function unavailableStore<T extends object>(name: string): T {
  const target: object = {};
  return new Proxy(target, {
    get(_target, prop) {
      if (prop === 'then') return undefined;
      return () => Promise.reject(new HarnessStoreUnavailableError(name));
    },
  }) as T;
}
