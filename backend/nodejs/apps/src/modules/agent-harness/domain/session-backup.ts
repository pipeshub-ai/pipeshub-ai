/**
 * Session backup and restore (P9): what a backup of one session holds, per
 * store, beside the erasure coverage it mirrors (`SESSION_DATA_STORES`). A
 * backup covers what erasure covers: every Mongo store of a session's data is
 * either copied for the session, copied for its org (what unseals it), or
 * excluded with the reason. C-14 fails when a store has no entry here.
 */
import { createHash } from 'node:crypto';

export type SessionBackupCoverage =
  { copy: 'session' } | { copy: 'org'; reason: string } | { excluded: string };

export const SESSION_BACKUP_FORMAT = 'pipeshub.session-backup/1';
/** The same archive from a PostgreSQL document store: one file per table, rows as `to_jsonb` gives them. */
export const SESSION_BACKUP_PG_FORMAT = 'pipeshub.session-backup.postgres/1';

const BACKFILL_RECORDS =
  'operator records of the deployment that ran the backfill (E7B)';

export const SESSION_BACKUP_COVERAGE: Readonly<
  Record<string, SessionBackupCoverage>
> = {
  chatSessions: { copy: 'session' },
  chatSessionMessages: { copy: 'session' },
  citations: { copy: 'session' },
  conversations: { copy: 'session' },
  agentconversations: { copy: 'session' },
  agentRuns: { copy: 'session' },
  agentNextRuns: { copy: 'session' },
  agentEvents: { copy: 'session' },
  agentCommandReceipts: { copy: 'session' },
  agentAdmissionReceipts: { copy: 'session' },
  sessionEvents: { copy: 'session' },
  agentSessionForks: { copy: 'session' },
  agentJournalPayloads: { copy: 'session' },
  agentWorkspaceManifests: { copy: 'session' },
  agentWorkspaceHeads: { copy: 'session' },
  agentWorkspaceWriters: { copy: 'session' },
  agentWorkspaceKeys: { copy: 'session' },
  agentEventCounters: { copy: 'session' },
  sessionStreamCounters: { copy: 'session' },
  agentHarnessDataKeys: {
    copy: 'org',
    reason:
      "the org's wrapped data keys: the session's sealed journal and its workspace key are sealed with them (wrapped under the deployment's KEK, which the archive never holds)",
  },
  agentAuditRecords: {
    excluded:
      "the org's audit chain: records chain across sessions, so a session's slice cannot be replayed into another chain; the source keeps it",
  },
  agentAuditHeads: { excluded: 'per-org chain head (see agentAuditRecords)' },
  agentAuditPending: {
    excluded:
      'audit entries on their way onto the chain (see agentAuditRecords)',
  },
  agentAuditAnchors: {
    excluded: 'per-org WORM anchors (see agentAuditRecords)',
  },
  agentHarnessLeases: { excluded: 'leader leases, no session data' },
  agentPrincipalErasures: {
    excluded:
      'deletions of the source deployment, ids only; a restored session needs none of them',
  },
  agentSandboxSnapshots: {
    excluded:
      'the index of cached sandbox snapshots (OWD-12): a snapshot only speeds a run up, so a restored session starts its next engine turn cold',
  },
  agentSandboxReaps: {
    excluded:
      "reaps of the source deployment's broker, ids only; the target's broker has none of those sandboxes",
  },
  agentHarnessBudgetAccounts: {
    excluded:
      'billing history stays with the deployment that spent it; a resumed run opens its account again',
  },
  agentHarnessBudgetReservations: {
    excluded: 'billing history (see agentHarnessBudgetAccounts)',
  },
  agentAdmissionQuotas: {
    excluded: 'per-org and per-actor rate windows, no session data',
  },
  agentSchedules: {
    excluded: "a user's schedules are principal data, not a session's (P8a)",
  },
  agentEngineSignIns: {
    excluded:
      "a person's own Claude sign-in record is principal data, not a session's (SIGNIN)",
  },
  agentHarnessInboxPreferences: {
    excluded:
      "a person's Inbox settings are principal data, not a session's (G2)",
  },
  agentHarnessRunEndNotices: {
    excluded:
      "a run notice's claim is principal data, and a restored session's runs are never told again (G2)",
  },
  agentHarnessRunWatchers: {
    excluded: 'open-stream leases live for a minute; nothing to restore (G2)',
  },
  agentMemories: {
    excluded:
      "a user's or agent's memories are principal data (P7.3); restoring a session never brings back a memory erased since",
  },
  agentErasures: {
    excluded:
      'agent erasures still to retry (H4), ids only; a restored session needs none of them',
  },
  agentScheduleCounters: {
    excluded:
      'schedule counts per org and actor (H2); the target counts its own schedules again at the next create',
  },
  agentScheduleFirings: {
    excluded:
      'ids, slot times and reason codes of firings; each fired run is its own session, backed up on its own (P8a)',
  },
  agentSessionErasures: {
    excluded: 'the erasure records of the deployment that ran them',
  },
  agentErasureBackfillPlans: { excluded: BACKFILL_RECORDS },
  agentErasureBackfillItems: { excluded: BACKFILL_RECORDS },
  agentErasureBackfillMarkers: { excluded: BACKFILL_RECORDS },
  notifications: {
    excluded:
      'inbox notices of the source deployment (a scheduled run waiting for approval); a restored run that waits notifies again',
  },
  outbox_events: {
    excluded:
      'dispatch hints of the source deployment; the target dispatches the restored runs itself (sweeper)',
  },
};

export interface SessionBackupManifest {
  format: typeof SESSION_BACKUP_FORMAT | typeof SESSION_BACKUP_PG_FORMAT;
  orgId: string;
  sessionId: string;
  createdAt: string;
  /** Per collection: documents and the sha256 of its NDJSON file. */
  collections: Record<string, { documents: number; sha256: string }>;
  /** sha256 over the sorted `name:sha256` lines: one change anywhere changes it. */
  digest: string;
}

export class SessionBackupRefused extends Error {
  constructor(
    readonly reasonCode:
      'session_exists' | 'archive_corrupt' | 'archive_format',
    message: string,
  ) {
    super(message);
    this.name = 'SessionBackupRefused';
  }
}

/** sha256 of an archive file's text as written (UTF-8): every backend's file digest, checked on restore. */
export const archiveFileDigest = (fileText: string): string =>
  createHash('sha256').update(fileText).digest('hex');

export const manifestDigest = (
  collections: SessionBackupManifest['collections'],
  sha256: (text: string) => string,
): string =>
  sha256(
    Object.entries(collections)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .map(([name, collection]) => `${name}:${collection.sha256}`)
      .join('\n'),
  );
