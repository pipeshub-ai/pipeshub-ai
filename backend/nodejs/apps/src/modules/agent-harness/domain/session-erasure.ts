/**
 * Session erasure (E7, ARCHITECTURE 5.7): deleting a conversation, or the
 * org's retention period passing, erases everything stored for the session.
 * One durable job per session runs these steps in order; each is idempotent,
 * so a job that crashes anywhere resumes at the step it was in.
 */
import { HarnessConflictError } from './errors';

export const ERASURE_STEPS = [
  'cancel_runs',
  'shred_workspace_key',
  'await_runs_terminal',
  'live_bus',
  'memories',
  'extension_stores',
  'artifacts',
  'traces',
  'workspaces',
  'sandbox_folders',
  'payloads',
  'journal',
  'conversation',
  'audit',
] as const;

export type ErasureStep = (typeof ERASURE_STEPS)[number];
export type ErasureReason = 'user_delete' | 'retention';

/** Items each step removed or tombstoned, by name; counts only, never ids. */
export type ErasureCounts = Record<string, number>;

export interface ErasureRequest {
  orgId: string;
  sessionId: string;
  reason: ErasureReason;
  /** Principal ref of who deleted it; null when retention expired it. */
  by: string | null;
}

/** The one org a batch of requests is for; null for none. A store writes a batch as one org's, so a mixed one is refused. */
export function requestsOrg(
  requests: readonly ErasureRequest[],
): string | null {
  const orgId = requests[0]?.orgId ?? null;
  if (requests.some((request) => request.orgId !== orgId)) {
    throw new Error('an erasure batch is of one org');
  }
  return orgId;
}

export type ErasureStatus = 'pending' | 'completed';

export interface ErasureJob extends ErasureRequest {
  status: ErasureStatus;
  /** The next step to run; null once completed. */
  step: ErasureStep | null;
  counts: ErasureCounts;
  /** Bumped by every claim: the fence a step's completion is checked against. */
  attempt: number;
  /** Consecutive failed attempts of the current step. */
  failures: number;
  requestedAt: number;
  nextAttemptAt: number;
  completedAt: number | null;
  lastErrorType: string | null;
}

/** What a step answers: done (with what it counted), or not yet (nothing failed). */
export type ErasureStepOutcome =
  | { kind: 'done'; counts?: ErasureCounts }
  | { kind: 'wait'; retryAfterMs: number; reason: string };

export const ERASURE_LEASE_NAME = 'agent-harness-session-erasure';
export const ERASURE_TICK_MS = 5_000;
/** Several missed ticks before another replica takes over erasure. */
export const ERASURE_LEASE_MS = 30_000;
/** A job one replica holds; a crashed holder's job is taken over after it. */
export const ERASURE_JOB_LEASE_MS = 2 * 60_000;
export const ERASURE_JOBS_PER_TICK = 10;
export const ERASURE_BACKOFF_BASE_MS = 5_000;
export const ERASURE_BACKOFF_MAX_MS = 5 * 60_000;
/** A step failing this often in a row is logged as an error, and still retried. */
export const ERASURE_ALERT_AFTER_FAILURES = 5;
/**
 * An agent erasure intent (H5) whose delete request never reported back is
 * first checked this long after the request: the query service's delete has
 * answered, or failed, by then.
 */
export const AGENT_DELETE_CONFIRM_AFTER_MS = 10 * 60_000;
/**
 * An intent whose agent is still live (or absent) this long after the
 * request is withdrawn without erasing: that delete was refused or failed.
 */
export const AGENT_DELETE_WITHDRAW_AFTER_MS = 60 * 60_000;
/** How soon the job looks again at runs that are still stopping. */
export const ERASURE_RUNS_POLL_MS = 5_000;
/** Artifact pages one step run asks for before it yields to other jobs. */
export const ERASURE_ARTIFACT_PAGES_PER_STEP = 50;
/** Run ids one call to the query service's trace erase names (its route's bound). */
export const TRACE_ERASE_PAGE = 50;
/** Attachments one call to the query service names (its route's bound). */
export const ATTACHMENT_ERASE_PAGE = 50;

export const erasureJobId = (orgId: string, sessionId: string): string =>
  `${orgId}:${sessionId}`;

export function nextErasureStep(step: ErasureStep): ErasureStep | null {
  const index = ERASURE_STEPS.indexOf(step);
  return ERASURE_STEPS[index + 1] ?? null;
}

/**
 * A job moves only to the step after its own (QA13-F3): a skipped step
 * (the workspace key not shredded, a store not erased) would complete an
 * erasure that left data behind.
 */
export function assertErasureAdvance(
  job: Pick<ErasureJob, 'step'>,
  next: ErasureStep | null,
): void {
  if (job.step === null || next !== nextErasureStep(job.step)) {
    throw new HarnessConflictError(
      'INVALID_TRANSITION',
      `An erasure at ${String(job.step)} moves only to the step after it, not ${String(next)}`,
    );
  }
}

/** Doubling from 5 s, capped at 5 min; erasure is never given up. */
export function erasureBackoffMs(failures: number): number {
  return Math.min(
    ERASURE_BACKOFF_BASE_MS * 2 ** Math.max(0, failures - 1),
    ERASURE_BACKOFF_MAX_MS,
  );
}

export const addCounts = (
  into: ErasureCounts,
  more: ErasureCounts | undefined,
): ErasureCounts => {
  const sum = { ...into };
  for (const [name, count] of Object.entries(more ?? {})) {
    sum[name] = (sum[name] ?? 0) + count;
  }
  return sum;
};

/** The command id that cancels one run for the erasure of its session (receipt-idempotent). */
export const erasureCancelCommandId = (
  sessionId: string,
  runId: string,
): string => `erase:${sessionId}:${runId}`;

/**
 * Where session data lives, and how erasure covers each store (C-14). A
 * store a later phase adds must be listed here with the step that erases it,
 * or with why it is kept; the cohesion check fails on a collection that is
 * not listed and on a step the runner does not have.
 */
export type ErasureCoverage =
  { step: ErasureStep } | { kept: string } | { deferred: string };

export interface SessionDataStore {
  store: string;
  kind: 'mongo' | 'redis' | 'graph' | 'blob' | 'object' | 'external';
  coverage: ErasureCoverage;
  /**
   * What the step leaves of the session on purpose: ids without content (a
   * tombstone). The erasure census (QA-15) accepts the session's ids in such
   * a store, never its content.
   */
  residue?: string;
}

export const SESSION_DATA_STORES: readonly SessionDataStore[] = [
  { store: 'chatSessions', kind: 'mongo', coverage: { step: 'conversation' } },
  {
    store: 'chatSessionMessages',
    kind: 'mongo',
    coverage: { step: 'conversation' },
  },
  { store: 'citations', kind: 'mongo', coverage: { step: 'conversation' } },
  // Frozen pre-chatSessions copies, migrated under the same _id.
  { store: 'conversations', kind: 'mongo', coverage: { step: 'conversation' } },
  {
    store: 'agentconversations',
    kind: 'mongo',
    coverage: { step: 'conversation' },
  },
  {
    store: 'agentRuns',
    kind: 'mongo',
    coverage: { step: 'journal' },
    residue:
      'the run as a terminal tombstone: ids, status and counters; input, definition, label and follow-up suggestions removed',
  },
  // Dropped before the runs are cancelled, so none is admitted during erasure (SS2).
  { store: 'agentNextRuns', kind: 'mongo', coverage: { step: 'cancel_runs' } },
  { store: 'agentEvents', kind: 'mongo', coverage: { step: 'journal' } },
  {
    store: 'agentCommandReceipts',
    kind: 'mongo',
    coverage: { step: 'journal' },
  },
  {
    store: 'agentAdmissionReceipts',
    kind: 'mongo',
    coverage: { step: 'journal' },
  },
  {
    store: 'sessionEvents',
    kind: 'mongo',
    coverage: { step: 'journal' },
    residue:
      'tombstoned envelopes: ids, seq and type, no content, so the stream stays gap-free',
  },
  // Fork requests (SS3) name the source and the fork; either side's erasure takes them.
  {
    store: 'agentSessionForks',
    kind: 'mongo',
    coverage: { step: 'journal' },
  },
  {
    store: 'agentJournalPayloads',
    kind: 'mongo',
    coverage: { step: 'payloads' },
  },
  {
    store: 'agentWorkspaceManifests',
    kind: 'mongo',
    coverage: { step: 'workspaces' },
  },
  {
    store: 'agentWorkspaceHeads',
    kind: 'mongo',
    coverage: { step: 'workspaces' },
  },
  {
    store: 'agentWorkspaceWriters',
    kind: 'mongo',
    coverage: { step: 'workspaces' },
  },
  {
    store: 'agentSandboxSnapshots',
    kind: 'mongo',
    coverage: { step: 'workspaces' },
    residue:
      'none: the rows name objects sealed with the session key shredded at shred_workspace_key (OWD-12)',
  },
  {
    store: 'agentWorkspaceKeys',
    kind: 'mongo',
    coverage: { step: 'shred_workspace_key' },
    residue:
      'the destroyed marker (no key material), so the key is never issued again',
  },
  {
    store: 'agentEventCounters',
    kind: 'mongo',
    coverage: {
      kept: 'per-session counters and feature names, no content; seq continuity (a waiting /compact focus is unset at cancel_runs)',
    },
  },
  {
    store: 'sessionStreamCounters',
    kind: 'mongo',
    coverage: {
      kept: 'per-stream last seq, no content; the tombstoned stream stays gap-free',
    },
  },
  {
    store: 'agentAuditRecords',
    kind: 'mongo',
    coverage: {
      kept: 'audit records hold ids, enums, counts and digests only (REPORT 6.16)',
    },
  },
  {
    store: 'agentAuditHeads',
    kind: 'mongo',
    coverage: { kept: 'per-org chain head' },
  },
  {
    store: 'agentAuditPending',
    kind: 'mongo',
    coverage: {
      kept: 'audit entries committed and not yet linked onto the chain: the same ids, enums, counts and digests as agentAuditRecords',
    },
  },
  {
    store: 'agentAuditAnchors',
    kind: 'mongo',
    coverage: { kept: 'per-org WORM anchors' },
  },
  {
    store: 'agentHarnessDataKeys',
    kind: 'mongo',
    coverage: { kept: 'per-org wrapped data keys, no session data' },
  },
  {
    store: 'agentPrincipalErasures',
    kind: 'mongo',
    coverage: {
      kept: 'org and user deletions on their way (CLEANUP-G1, G2): ids, step, times, counts, and the conversations moved to an admin; removed when the deletion completes',
    },
  },
  {
    store: 'agentSandboxReaps',
    kind: 'mongo',
    coverage: {
      kept: 'sandbox reaps the broker refused (CLEANUP-G6): org, run and attempt ids, deleted once the broker answers; no session data',
    },
  },
  {
    store: 'agentHarnessLeases',
    kind: 'mongo',
    coverage: { kept: 'leader leases, no session data' },
  },
  {
    store: 'agentHarnessBudgetAccounts',
    kind: 'mongo',
    coverage: {
      kept: 'run ids, caps and spent amounts only (P6.5): spend is billing history, not content',
    },
  },
  {
    store: 'agentHarnessBudgetReservations',
    kind: 'mongo',
    coverage: {
      kept: 'run ids, caller keys and amounts only (P6.5): spend is billing history, not content',
    },
  },
  {
    store: 'agentAdmissionQuotas',
    kind: 'mongo',
    coverage: { kept: 'per-org and per-actor rate windows, no session data' },
  },
  {
    store: 'agentSchedules',
    kind: 'mongo',
    coverage: {
      kept: "a user's schedules are principal data (PRINCIPAL_DATA_STORES): deleted with the user or the org (CLEANUP-G2), disabled with their template dropped when their agent is deleted (AGENT_DATA_STORES); no session data",
    },
  },
  {
    store: 'agentEngineSignIns',
    kind: 'mongo',
    coverage: {
      kept: "a person's own Claude sign-in record (SIGNIN) is principal data (PRINCIPAL_DATA_STORES): removed when the user or org is deleted; no session data",
    },
  },
  {
    store: 'agentHarnessInboxPreferences',
    kind: 'mongo',
    coverage: {
      kept: "a person's Inbox settings (G2: muted workflows, the pause, notice channels) are principal data (PRINCIPAL_DATA_STORES); no session data",
    },
  },
  {
    store: 'agentHarnessRunEndNotices',
    kind: 'mongo',
    coverage: {
      kept: "that a run's finished notice was decided (G2): a run id and its owner, principal data (PRINCIPAL_DATA_STORES), expired after 30 days; the notice itself is in notifications",
    },
  },
  {
    store: 'agentHarnessRunWatchers',
    kind: 'mongo',
    coverage: {
      kept: "a person's open-stream leases (G2), principal data (PRINCIPAL_DATA_STORES) that expire within a minute of the stream closing",
    },
  },
  {
    store: 'agentErasures',
    kind: 'mongo',
    coverage: {
      kept: 'agent erasure intents (H5: written before each agent delete) and erasures that failed after it (H4), org and agent ids only, removed once erased or withdrawn; no session data',
    },
  },
  {
    store: 'agentScheduleCounters',
    kind: 'mongo',
    coverage: {
      kept: 'per-org and per-actor schedule counts (H2), ids and numbers only; no session data',
    },
  },
  {
    store: 'agentScheduleFirings',
    kind: 'mongo',
    coverage: {
      kept: 'ids, slot times and reason codes only (P8a); each fired run is its own session, which this registry erases',
    },
  },
  {
    store: 'agentSessionErasures',
    kind: 'mongo',
    coverage: {
      kept: 'the erasure record itself: ids, reason, counts; answers "erased"',
    },
  },
  // E7B: the operator's backfill of deletions from before erasure existed.
  {
    store: 'agentErasureBackfillPlans',
    kind: 'mongo',
    coverage: {
      kept: 'the operator record of a backfill: digest, org ids, counts by store, cursor; no session id or content',
    },
  },
  {
    store: 'agentErasureBackfillItems',
    kind: 'mongo',
    coverage: {
      kept: 'what a backfill covered: org and session ids, a fingerprint digest, counts and state; no content',
    },
  },
  {
    store: 'agentErasureBackfillMarkers',
    kind: 'mongo',
    coverage: {
      kept: 'one completion per org: digest, operator and counts',
    },
  },
  {
    store: 'outbox_events',
    kind: 'mongo',
    coverage: {
      kept: 'dispatch hints carry run ids only; published rows expire after 7 days',
    },
  },
  // Every memory derived from the session, whatever its scope (P7.3).
  { store: 'agentMemories', kind: 'mongo', coverage: { step: 'memories' } },
  { store: 'liveBus', kind: 'redis', coverage: { step: 'live_bus' } },
  // The worker's per-run spend (Python `RedisSpendCounter`, `harness:spend:`).
  {
    store: 'spendCounters',
    kind: 'redis',
    coverage: {
      kept: 'org and run ids with spent amounts only; each key expires 14 days after its last charge',
    },
  },
  { store: 'artifactRecords', kind: 'graph', coverage: { step: 'artifacts' } },
  { store: 'artifactBlobs', kind: 'blob', coverage: { step: 'artifacts' } },
  // Model and tool traces a deployment with Opik on sent there (prompts, tool arguments), by conversation and run.
  { store: 'opikTraces', kind: 'external', coverage: { step: 'traces' } },
  // Skill drafts post-run learning wrote from the session's runs (CLEANUP-G7), erased by the query service's
  // conversation erase with the artifacts; drafts from before they named a session are left to admin review.
  {
    store: 'agentSkillCandidates',
    kind: 'graph',
    coverage: { step: 'artifacts' },
  },
  {
    store: 'workspaceObjects',
    kind: 'object',
    coverage: { step: 'shred_workspace_key' },
  },
  {
    store: 'skillBundles',
    kind: 'object',
    coverage: { step: 'workspaces' },
  },
  {
    store: 'engineState',
    kind: 'object',
    coverage: { step: 'shred_workspace_key' },
  },
  {
    // A durable engine run's sandbox caches (P5.9b), sealed with the session workspace key.
    store: 'sandboxSnapshots',
    kind: 'object',
    coverage: { step: 'shred_workspace_key' },
  },
  {
    // The broker's data volume: an engine session's workspace (and its forks)
    // under <install>/<org>/<session>/, kept between sandboxes (E1).
    store: 'sandboxFolders',
    kind: 'blob',
    coverage: { step: 'sandbox_folders' },
  },
  // A scheduled run's inbox notice (P8a) names the conversation it waits in.
  { store: 'notifications', kind: 'mongo', coverage: { step: 'conversation' } },
  // Records, files, edges, mapping and stored bytes, through the query service; the upload writes no vectors.
  // Uploads no message carried go at the artifacts step (by the conversation they were made in) or, never
  // carried, a day on (CLEANUP-G5).
  {
    store: 'chatAttachments',
    kind: 'graph',
    coverage: { step: 'conversation' },
  },
];

/**
 * Data a user or an org owns outside any session (C-14, P2.4b): deleting the
 * user or the org erases it, through the named hook, before the principal is
 * marked deleted. A principal-owned store added later is listed here with its
 * hooks and the deletion paths that call them, or C-14 fails.
 */
export interface PrincipalDataStore {
  store: string;
  kind: 'config' | 'mongo';
  location: string;
  /** The property each deletion path holds the store's hooks in. */
  field: string;
  onUserDeletion: 'eraseUser';
  onOrgDeletion: 'eraseOrg';
  /** `file#method` of each deletion path; C-14 checks each calls a hook. */
  callers: readonly string[];
}

export const PRINCIPAL_DATA_STORES: readonly PrincipalDataStore[] = [
  {
    store: 'modelAccounts',
    kind: 'config',
    location:
      '/services/agentHarness/credentials/{orgId}/users/{userId}/{provider}, /services/agentHarness/credentials/{orgId}/cloud/{accountId}',
    field: 'modelAccounts',
    onUserDeletion: 'eraseUser',
    onOrgDeletion: 'eraseOrg',
    callers: [
      'modules/user_management/controller/users.controller.ts#deleteUser',
      'modules/user_management/controller/org.controller.ts#deleteOrganization',
      'modules/agent-harness/binders/erasure.ts#orgPrincipalErasers',
      'modules/user_management/services/service-accounts.service.ts#remove',
    ],
  },
  {
    // A service account cannot own a schedule (P8a), so its removal has nothing to disable.
    store: 'agentSchedules',
    kind: 'mongo',
    location: 'agentSchedules (input template, webhook secret hash)',
    field: 'schedules',
    onUserDeletion: 'eraseUser',
    onOrgDeletion: 'eraseOrg',
    callers: [
      'modules/user_management/controller/users.controller.ts#deleteUser',
      'modules/user_management/controller/org.controller.ts#deleteOrganization',
      'modules/agent-harness/binders/erasure.ts#orgPrincipalErasers',
    ],
  },
  {
    // A user's memories, and every memory of an org (P7.3).
    store: 'agentMemories',
    kind: 'mongo',
    location: 'agentMemories (ownerId, orgId)',
    field: 'memories',
    onUserDeletion: 'eraseUser',
    onOrgDeletion: 'eraseOrg',
    callers: [
      'modules/user_management/controller/users.controller.ts#deleteUser',
      'modules/user_management/controller/org.controller.ts#deleteOrganization',
      'modules/agent-harness/binders/erasure.ts#orgPrincipalErasers',
      'modules/user_management/services/service-accounts.service.ts#remove',
    ],
  },
  {
    // A person's own Claude sign-in (SIGNIN): their email and plan kind as Claude Code reported them, the org's switch, and any
    // sandbox still signed in. A service account can never turn it on, so its removal has nothing to end.
    store: 'engineSignIns',
    kind: 'mongo',
    location:
      'agentEngineSignIns (<orgId>:u:<userId>, <orgId>:org); live sandboxes through the sign-in relay',
    field: 'engineSignIns',
    onUserDeletion: 'eraseUser',
    onOrgDeletion: 'eraseOrg',
    callers: [
      'modules/user_management/controller/users.controller.ts#deleteUser',
      'modules/user_management/controller/org.controller.ts#deleteOrganization',
      'modules/agent-harness/binders/erasure.ts#orgPrincipalErasers',
    ],
  },
  {
    // G2 (ADR-INBOX-01): a person's Inbox settings, the claims of their runs' finished notices and their watch leases.
    store: 'inbox',
    kind: 'mongo',
    location:
      'agentHarnessInboxPreferences, agentHarnessRunEndNotices, agentHarnessRunWatchers (orgId, userId)',
    field: 'inbox',
    onUserDeletion: 'eraseUser',
    onOrgDeletion: 'eraseOrg',
    callers: [
      'modules/user_management/controller/users.controller.ts#deleteUser',
      'modules/user_management/controller/org.controller.ts#deleteOrganization',
      'modules/agent-harness/binders/erasure.ts#orgPrincipalErasers',
    ],
  },
  {
    // A person's own toolset instances and MCP servers (main #4324), with every sign-in to them, their tool
    // rules and owner pointers, and the person's sign-ins to the org's instances; erased by the connectors
    // service (it runs the token refreshers). With CONTROL_PLANE_CREDENTIALS on, their bindings go with
    // their installations (ControlPlaneUserEraser; the org's control-plane stores at `org_stores`).
    // A service account is an ordinary user record, so it can add its own through the API like anyone else (main
    // refuses a personal action only on a service-account *agent*); removing the account erases them too.
    store: 'personalInstances',
    kind: 'config',
    location:
      '/services/toolset-user-instances/{orgId}/{userId}/{instanceId}, /services/toolset-personal-owners/{instanceId}, /services/toolset-tool-rules/{instanceId}, /services/toolsets/{instanceId}/*, /services/mcp/user-instances/{orgId}/{userId}/{instanceId}, /services/mcp/credentials/{instanceId}/{userId}',
    field: 'personalInstances',
    onUserDeletion: 'eraseUser',
    onOrgDeletion: 'eraseOrg',
    callers: [
      'modules/user_management/controller/users.controller.ts#deleteUser',
      'modules/user_management/controller/org.controller.ts#deleteOrganization',
      'modules/agent-harness/binders/erasure.ts#orgPrincipalErasers',
      'modules/user_management/services/service-accounts.service.ts#remove',
    ],
  },
];

/**
 * Data an agent owns (C-14, H2): deleting the agent erases it through the
 * named hook once the query service has deleted the agent (the delete is the
 * query service's, and only it decides who may delete). An agent-owned store
 * added later is listed here, or C-14 fails.
 */
export interface AgentDataStore {
  store: string;
  kind: 'mongo';
  location: string;
  /** The parameter the deletion path holds the store's hooks in. */
  field: string;
  onAgentDeletion: 'eraseAgent';
  /** `file#export` of each deletion path; C-14 checks each calls the hook after the delete. */
  callers: readonly string[];
  /**
   * Where the erasure waits until it is done (H4, H5): the parameter the
   * deletion path records its intent (before the delete) and a failed
   * erasure (after it) through, the collection, and the sweeper part that
   * drains it, confirming an unconfirmed intent through `confirm` first.
   */
  retry: { field: string; store: string; part: string; confirm: string };
}

export const AGENT_DATA_STORES: readonly AgentDataStore[] = [
  {
    // An agent's own memories (P7.3 scope `agent`); its users' memories are theirs.
    store: 'agentMemories',
    kind: 'mongo',
    location: 'agentMemories (scope agent, agentKey)',
    field: 'agentData',
    onAgentDeletion: 'eraseAgent',
    callers: [
      'modules/enterprise_search/controller/es_controller.ts#deleteAgent',
    ],
    retry: {
      field: 'retries',
      store: 'agentErasures',
      part: 'agent_erasures',
      confirm: 'deletions',
    },
  },
  {
    // The agent's schedules (P8a): disabled with their template and webhook secret dropped, so none fires again.
    store: 'agentSchedules',
    kind: 'mongo',
    location: 'agentSchedules (agentKey)',
    field: 'agentData',
    onAgentDeletion: 'eraseAgent',
    callers: [
      'modules/enterprise_search/controller/es_controller.ts#deleteAgent',
    ],
    retry: {
      field: 'retries',
      store: 'agentErasures',
      part: 'agent_erasures',
      confirm: 'deletions',
    },
  },
];
