/**
 * Org retention (E7 + T5): an org may set a period after which its
 * conversations are erased by the same job a delete enqueues. The product
 * default is no automatic expiry, configurable per org.
 */
export const SESSION_RETENTION_MIN_DAYS = 1;
export const SESSION_RETENTION_MAX_DAYS = 3650;
export const DAY_MS = 24 * 60 * 60 * 1000;

/** The stored document: one entry per org that set a period; absent = no expiry. */
export interface SessionRetentionDocument {
  orgs: Record<string, { days: number; updatedAt: number; updatedBy: string }>;
}

export interface OrgSessionRetention {
  orgId: string;
  days: number;
}

/** Conversations last active before this are past the period. */
export const retentionCutoff = (now: number, days: number): number =>
  now - days * DAY_MS;

/** T5 windows: constants per release, never env vars (RT-6). */
export const RETENTION_SWEEP_LEASE_NAME = 'agent-harness-retention';
export const RETENTION_SWEEP_INTERVAL_MS = 60_000;
export const RETENTION_SWEEP_LEASE_MS = 3 * RETENTION_SWEEP_INTERVAL_MS;
/** An unreferenced payload is collected only after this, and only once its writer run ended. */
export const PAYLOAD_GC_GRACE_MS = DAY_MS;
/** Command and admission receipts answer replays for this long. */
export const RECEIPT_TTL_MS = 7 * DAY_MS;
/** Discarded drafts are kept this long before their blobs and records go. */
export const DISCARDED_DRAFT_GRACE_MS = DAY_MS;
/**
 * A "continue privately" fork whose conversation never committed this long after it began is abandoned: its
 * copies (artifacts, workspace key, manifests, objects) are erased like an expired conversation's (CLEANUP-G4).
 * Far longer than any fork attempt, so a running one is never taken.
 */
export const ABANDONED_FORK_AFTER_MS = 60 * 60_000;
/** A chat attachment no message carries this long after its upload is erased (CLEANUP-G5). */
export const UNSENT_ATTACHMENT_GRACE_MS = DAY_MS;
/** How often a replica's sweeper asks the query service for waiting uploads, org by org. */
export const UNSENT_ATTACHMENT_SWEEP_MS = 60 * 60_000;
/** Pages of waiting uploads one org's pass reads before it yields to the next org. */
export const UNSENT_ATTACHMENT_PAGES = 10;
/** Items each pass of the sweeper handles per kind; a backlog drains over later passes. */
export const RETENTION_SWEEP_BATCH = 200;
