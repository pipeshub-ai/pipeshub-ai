/**
 * Erasing an org or a user after its deletion (CLEANUP-G1, G2).
 *
 * A user's deletion erases their private conversations through the session
 * erasure jobs and moves the ones shared with others to the org's earliest
 * active admin; the durable job journals each move and erases the uploads they
 * never sent. An org's deletion erases everything the org holds through its
 * own durable job: its sessions (each through its session erasure job), its
 * documents, its keys and its audit chain. Its steps are built in and may be
 * replaced (`HarnessEdition.orgErasure`).
 */

export const ORG_ERASURE_STEPS = [
  'cancel_runs',
  'end_sandboxes',
  'org_event',
  'sessions',
  'await_sessions',
  'query_service',
  'sandbox_folders',
  'principals',
  'org_stores',
  'redis',
  'data_keys',
  'audit_retention',
  'audit',
] as const;

export const USER_ERASURE_STEPS = [
  'journal_transfers',
  'unsent_uploads',
  'control_plane',
  'cost_ledger',
  'engine_choices',
] as const;

export type OrgErasureStep = (typeof ORG_ERASURE_STEPS)[number];
export type UserErasureStep = (typeof USER_ERASURE_STEPS)[number];
export type PrincipalErasureStep = OrgErasureStep | UserErasureStep;

/** A shared conversation the deleted user owned, and the admin it moved to. */
export interface OwnerTransfer {
  sessionId: string;
  to: string;
}

export interface PrincipalErasureJob {
  kind: 'org' | 'user';
  orgId: string;
  /** The deleted user; null for an org. */
  userId: string | null;
  /** Principal ref of who deleted it. */
  by: string;
  step: PrincipalErasureStep;
  requestedAt: number;
  auditRetentionDays: number;
  /** Org: when the audit chain may go (set once the data keys are destroyed). */
  auditRemoveAfter: number | null;
  /** Org `sessions`: the last session id enqueued. */
  cursor: string | null;
  /** User: the conversations moved to an admin, and how many are journaled. */
  transfers: OwnerTransfer[];
  journaled: number;
  counts: Record<string, number>;
  /** State the org's erasure steps keep on the job beyond these fields (`HarnessEdition.orgErasure`); null: none. */
  extension: Readonly<Record<string, unknown>> | null;
  failures: number;
  nextAttemptAt: number;
  lastErrorType: string | null;
}

export const orgErasureJobId = (orgId: string): string => `${orgId}:org`;
export const userErasureJobId = (orgId: string, userId: string): string =>
  `${orgId}:u:${userId}`;

export function nextPrincipalStep(
  job: Pick<PrincipalErasureJob, 'kind' | 'step'>,
): PrincipalErasureStep | null {
  const steps: readonly PrincipalErasureStep[] =
    job.kind === 'org' ? ORG_ERASURE_STEPS : USER_ERASURE_STEPS;
  return steps[steps.indexOf(job.step) + 1] ?? null;
}

/** Sessions an org's erasure enqueues in one pass. */
export const ORG_SESSIONS_PER_PASS = 200;
/** How soon an org's erasure looks again at its sessions still being erased. */
export const ORG_SESSIONS_POLL_MS = 30_000;

/** Principal erasure jobs one sweep runs. */
export const PRINCIPAL_ERASURES_PER_TICK = 5;

/**
 * Where an org's data lives outside its sessions' own erasure, and what the
 * org's erasure does with it (C-14 holds the list complete): `org_stores`
 * deletes the org's documents, `data_keys` destroys its keys last, `audit`
 * removes its chain after the audit retention period; `principals` runs the
 * user and org hooks; `none` means the store holds nothing of an org.
 */
export type OrgStoreCoverage =
  'org_stores' | 'principals' | 'data_keys' | 'audit' | { none: string };

/**
 * Collections no model of this build defines that an install may still hold
 * (written by an earlier build, or by an edition): each stays in the erasure
 * inventories, so an org's erasure empties it and none is silently uncovered.
 */
export const UNMODELLED_COLLECTIONS: readonly string[] = [
  'agentAuditChainChecks',
];

export const ORG_STORE_COVERAGE: Readonly<Record<string, OrgStoreCoverage>> = {
  chatSessions: 'org_stores',
  chatSessionMessages: 'org_stores',
  citations: 'org_stores',
  conversations: 'org_stores',
  agentconversations: 'org_stores',
  agentRuns: 'org_stores',
  agentNextRuns: 'org_stores',
  agentEvents: 'org_stores',
  agentCommandReceipts: 'org_stores',
  agentAdmissionReceipts: 'org_stores',
  sessionEvents: 'org_stores',
  agentSessionForks: 'org_stores',
  agentJournalPayloads: 'org_stores',
  agentWorkspaceManifests: 'org_stores',
  agentWorkspaceHeads: 'org_stores',
  agentWorkspaceWriters: 'org_stores',
  agentWorkspaceKeys: 'org_stores',
  agentEventCounters: 'org_stores',
  sessionStreamCounters: 'org_stores',
  agentAuditRecords: 'audit',
  agentAuditHeads: 'audit',
  agentAuditPending: 'audit',
  agentAuditAnchors: 'audit',
  agentAuditChainChecks: 'org_stores',
  agentHarnessDataKeys: 'data_keys',
  agentSandboxReaps: 'org_stores',
  agentSandboxSnapshots: 'org_stores',
  agentHarnessLeases: { none: 'leader leases are deployment-wide' },
  agentHarnessBudgetAccounts: 'org_stores',
  agentHarnessBudgetReservations: 'org_stores',
  agentAdmissionQuotas: 'org_stores',
  agentSchedules: 'principals',
  agentEngineSignIns: 'principals',
  agentHarnessInboxPreferences: 'principals',
  agentHarnessRunEndNotices: 'principals',
  agentHarnessRunWatchers: 'principals',
  agentErasures: 'org_stores',
  agentScheduleCounters: 'org_stores',
  agentScheduleFirings: 'org_stores',
  agentSessionErasures: 'org_stores',
  agentErasureBackfillPlans: 'org_stores',
  agentErasureBackfillItems: 'org_stores',
  agentErasureBackfillMarkers: 'org_stores',
  agentPrincipalErasures: {
    none: "the org's own erasure job, removed when the job completes",
  },
  outbox_events: 'org_stores',
  agentMemories: 'principals',
  notifications: 'org_stores',
  agentHarnessLiveness: { none: 'deployment-wide heartbeats' },
  agentHarnessProbes: { none: 'one deployment-wide probe document' },
  agentDispatchOutbox: 'org_stores',
  cpBindings: 'org_stores',
  cpCapabilityLedger: 'org_stores',
  cpCatalogItems: 'org_stores',
  cpGrants: 'org_stores',
  cpInstallationHistory: 'org_stores',
  cpInstallations: 'org_stores',
  cpMigrationLedger: 'org_stores',
  cpOrgHeads: 'org_stores',
  cpPolicyHistory: 'org_stores',
  cpPolicyLayers: 'org_stores',
  searches: { none: 'semantic search history: the platform deletes it' },
};
