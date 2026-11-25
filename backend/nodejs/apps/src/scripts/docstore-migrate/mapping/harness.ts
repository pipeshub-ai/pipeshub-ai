import type { HeldCollection, IgnoredCollection } from './types';

/**
 * The agent harness's and the control plane's collections (R45-MIGR). Each is either not copied because it is plumbing that the
 * move makes stale (IGNORED: a lease, a heartbeat, a probe, a five-minute ticket), or its data must survive a move: a carrier
 * copies it with the rest of its store group (`../carry`, R45-CARRY), or, while none does, it is held and preflight refuses a move
 * while it holds documents.
 *
 * Why carried and not mapped as the documents stand: the PostgreSQL stores are not the Mongo documents in another place. Most
 * documents keep no `_id` in PostgreSQL, so a delete seen by the follower names no row without the key map; some rows belong to no org
 * (the key map's deployment-wide form); the audit chain (records, heads, pending, anchors) is hashed and must move as one; the data keys
 * open every sealed value; the claim-checked payloads must stay readable by the names the journal, the runs and the receipts give them
 * (either document store reads either store's names, `payload-store/names.ts`); the control plane's revisions are carried exactly. A
 * copy that verified document by document would pass and leave such data unusable, so each group has its own carrier, written and
 * tested with the stores (R45-CARRY, R46-CARRY; ADR-DOCMIG-01 lists the carried groups and the ones still held).
 */
const t = (...names: string[]): string[] => names.map((n) => `harness.${n}`);

const DURABLE_RUN =
  'durable run state (runs, their journal, counters and queued work): a run resumed after the move needs it';
const IDEMPOTENCY =
  'idempotency receipts kept while a run is open: a retried command or admission after the move would run twice without them';
const LEDGER = 'the spend ledger: run budgets are enforced from it';
const AUDIT =
  'the hash-chained audit trail: records, chain head, unlinked entries and anchors move together or the chain no longer verifies';
const EDITION =
  'written by an earlier build or by an edition, never by this one (UNMODELLED_COLLECTIONS): an install may still hold it';
const PERSON =
  'settings and data people made (sign-ins, memories, schedules, Inbox settings, review candidates)';
const WORKSPACE =
  'conversation workspaces and their per-session keys: without them the files and every sealed value are lost';
const ERASURE =
  'erasure and deletion jobs in flight and their completion records: dropping one leaves data that should be erased';
const CONTROL_PLANE =
  "the control plane: each org's policy, catalog, installations, grants, bindings, ledgers and revision heads";

export const HARNESS_HELD: HeldCollection[] = [
  { collection: 'agentRuns', reason: DURABLE_RUN, tables: t('runs') },
  { collection: 'agentEvents', reason: DURABLE_RUN, tables: t('run_events') },
  {
    collection: 'agentEventCounters',
    reason: `${DURABLE_RUN}; a counter restarted at zero would reuse event numbers`,
    tables: t('session_counters'),
  },
  { collection: 'agentNextRuns', reason: DURABLE_RUN, tables: t('next_runs') },
  {
    collection: 'sessionEvents',
    reason: `${DURABLE_RUN}; claim-check names inside it change store`,
    tables: t('session_events'),
  },
  {
    collection: 'sessionStreamCounters',
    reason: `${DURABLE_RUN}; a counter restarted at zero would reuse journal numbers`,
    tables: t('stream_counters'),
  },
  {
    collection: 'agentJournalPayloads',
    reason: `${DURABLE_RUN}; the claim-checked payloads, named per store`,
    tables: t('journal_payloads'),
  },
  {
    collection: 'agentSessionForks',
    reason: 'fork records: a repeated "continue privately" would fork twice',
    tables: t('session_forks'),
  },
  {
    collection: 'agentSandboxReaps',
    reason:
      'sandboxes the broker still has to remove: dropped, they are never reaped',
    tables: t('sandbox_reaps'),
  },
  {
    collection: 'agentSandboxSnapshots',
    reason:
      'the index of stored sandbox snapshots that retention and erasure delete by: dropped, the objects are orphaned',
    tables: t('sandbox_snapshots'),
  },
  {
    collection: 'agentCommandReceipts',
    reason: IDEMPOTENCY,
    tables: t('command_receipts'),
  },
  {
    collection: 'agentAdmissionReceipts',
    reason: IDEMPOTENCY,
    tables: t('admission_receipts'),
  },
  {
    collection: 'agentAdmissionQuotas',
    reason: 'admission windows and holds of the org and actor quotas',
    tables: t('admission_quotas'),
  },
  {
    collection: 'agentHarnessBudgetAccounts',
    reason: LEDGER,
    tables: t('budget_accounts'),
  },
  {
    collection: 'agentHarnessBudgetReservations',
    reason: LEDGER,
    tables: t('budget_reservations'),
  },
  {
    collection: 'agentAuditRecords',
    reason: AUDIT,
    tables: t('audit_records'),
  },
  { collection: 'agentAuditHeads', reason: AUDIT, tables: t('audit_heads') },
  {
    collection: 'agentAuditPending',
    reason: AUDIT,
    tables: t('audit_pending'),
  },
  {
    collection: 'agentAuditAnchors',
    reason: AUDIT,
    tables: t('audit_anchors'),
  },
  {
    collection: 'agentAuditChainChecks',
    reason: `the audit chain's last check; ${EDITION}`,
    tables: t('audit_chain_checks'),
  },
  {
    collection: 'agentHarnessDataKeys',
    reason:
      "each org's wrapped data keys: every sealed workspace, session and payload is opened with them",
    tables: t('data_keys'),
  },
  {
    collection: 'agentEngineSignIns',
    reason: PERSON,
    tables: t('engine_signins'),
  },
  { collection: 'agentMemories', reason: PERSON, tables: t('memories') },
  { collection: 'agentSchedules', reason: PERSON, tables: t('schedules') },
  {
    collection: 'agentScheduleFirings',
    reason: `${PERSON}: a schedule's fire history, which also keeps a slot from firing twice`,
    tables: t('schedule_firings'),
  },
  {
    collection: 'agentScheduleCounters',
    reason: `${PERSON}: schedule limits and the webhook replay guard`,
    tables: t('schedule_counters'),
  },
  {
    collection: 'agentHarnessInboxPreferences',
    reason: PERSON,
    tables: t('inbox_preferences'),
  },
  {
    collection: 'agentHarnessRunEndNotices',
    reason:
      'the once-only claims of "your run finished" notices: dropped, a notice can be sent twice',
    tables: t('run_end_notices'),
  },
  {
    collection: 'agentWorkspaceHeads',
    reason: WORKSPACE,
    tables: t('workspace_heads'),
  },
  {
    collection: 'agentWorkspaceManifests',
    reason: WORKSPACE,
    tables: t('workspace_manifests'),
  },
  {
    collection: 'agentWorkspaceKeys',
    reason: `${WORKSPACE}; a shredded key's tombstone keeps a new key from being issued`,
    tables: t('workspace_keys'),
  },
  {
    collection: 'agentSessionErasures',
    reason: ERASURE,
    tables: t('session_erasures'),
  },
  { collection: 'agentErasures', reason: ERASURE, tables: t('agent_erasures') },
  {
    collection: 'agentPrincipalErasures',
    reason: ERASURE,
    tables: t('principal_erasures'),
  },
  {
    collection: 'agentErasureBackfillPlans',
    reason: ERASURE,
    tables: t('erasure_backfill_plans'),
  },
  {
    collection: 'agentErasureBackfillItems',
    reason: ERASURE,
    tables: t('erasure_backfill_items'),
  },
  {
    collection: 'agentErasureBackfillMarkers',
    reason: ERASURE,
    tables: t('erasure_backfill_markers'),
  },
  {
    collection: 'cpOrgHeads',
    reason: `${CONTROL_PLANE}; a revision restarted at one would match a cached ETag of another state`,
    tables: t('cp_org_heads', 'cp_deployment_head'),
  },
  {
    collection: 'cpPolicyLayers',
    reason: CONTROL_PLANE,
    tables: t('cp_policy_layers'),
  },
  {
    collection: 'cpPolicyHistory',
    reason: CONTROL_PLANE,
    tables: t('cp_policy_history'),
  },
  {
    collection: 'cpMigrationLedger',
    reason: CONTROL_PLANE,
    tables: t('cp_migration_ledger'),
  },
  {
    collection: 'cpCatalogItems',
    reason: CONTROL_PLANE,
    tables: t('cp_catalog_items'),
  },
  {
    collection: 'cpInstallations',
    reason: CONTROL_PLANE,
    tables: t('cp_installations'),
  },
  {
    collection: 'cpInstallationHistory',
    reason: CONTROL_PLANE,
    tables: t('cp_installation_history'),
  },
  { collection: 'cpGrants', reason: CONTROL_PLANE, tables: t('cp_grants') },
  {
    collection: 'cpCapabilityLedger',
    reason: CONTROL_PLANE,
    tables: t('cp_capability_ledger'),
  },
  { collection: 'cpBindings', reason: CONTROL_PLANE, tables: t('cp_bindings') },
];

export const HARNESS_IGNORED: IgnoredCollection[] = [
  {
    collection: 'agentHarnessLeases',
    reason:
      'leader leases of the processes the move stops; the first process after it takes the lease',
    tables: t('leases'),
  },
  {
    collection: 'agentHarnessLiveness',
    reason:
      "the control plane's heartbeat; the first one after the move starts it again (no gap is reported for the stop)",
    tables: t('liveness'),
  },
  {
    collection: 'agentHarnessRunWatchers',
    reason:
      'watch leases of open streams; every stream ends when the application stops for the move',
    tables: t('run_watchers'),
  },
  {
    collection: 'agentWorkspaceWriters',
    reason:
      "a run's write lease on a workspace; stale once the application stops, and an expired lease is taken over",
    tables: t('workspace_writers'),
  },
  {
    collection: 'agentHarnessProbes',
    reason:
      'the journal durability probe writes one scratch document; PostgreSQL needs none',
  },
  {
    collection: 'agentDispatchOutbox',
    reason:
      'rows of the relay before P0.6 that nothing reads; dispatches go through the shared outbox',
  },
];
