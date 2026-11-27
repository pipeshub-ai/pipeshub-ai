import { Model, Schema } from 'mongoose';
import {
  EXECUTION_STATUSES,
  RUN_OUTCOMES,
} from '../../../../../modules/agent-harness/contracts/generated/ext/pipeshub/enums';
import { CUT_SHORT_REASONS } from '../../../../../modules/agent-harness/contracts/generated/core/enums';
import {
  HarnessEvent,
  HarnessRun,
} from '../../../../../modules/agent-harness/domain/execution';
import { HARNESS_MAJORS } from '../../../../../modules/agent-harness/domain/rollout';
import { AgentRunDefinition } from '../../../../../modules/agent-harness/domain/agent-definition';
import type { RunSurface } from '../../../../../modules/agent-harness/domain/admission';
import type { RunVia } from '../../../../../modules/agent-harness/domain/run-spec';
import type { PinnedEngineProfile } from '../../../../../modules/agent-harness/domain/engine-profiles';
import type { PinnedModelAccess } from '../../../../../modules/agent-harness/domain/model-access';
import type { RunAudience } from '../../../../../modules/agent-harness/domain/session-audience';
import type { MemoryPin } from '../../../../../modules/agent-harness/domain/memory';
import type { SessionRepository } from '../../../../../modules/agent-harness/domain/repository';
import type { RunGates } from '../../../../../modules/agent-harness/domain/run-gates';
import type { AnsweredRunPin } from '../../../../../modules/agent-harness/domain/answered-run';
import type { TurnLabel } from '../../../../../modules/agent-harness/domain/turn-labels';
import type { RunTrigger } from '../../../../../modules/agent-harness/domain/schedules';
import type { QuotaHold } from '../../../../../modules/agent-harness/domain/admission-quotas';
import { harnessModel } from '../../schema/harness-model.schema';
import type { WorkspaceHeadDocument } from '../document';

export {
  runFromDocument,
  storedHistoryRef,
  type StoredRunInput,
  type WorkspaceHeadDocument,
} from '../document';

export const RUNS_BY_SESSION_INDEX = 'org_session';
export const RECEIPTS_BY_AGE_INDEX = 'created';
export const ADMISSION_RECEIPTS_BY_SESSION_INDEX = 'result_session';
export const RUNS_BY_PARENT_INDEX = 'org_parent_status';
export const CHILD_RUNS_BY_STATUS_INDEX = 'child_status_created';
export const NEXT_RUNS_BY_SESSION_INDEX = 'org_session_status_created';
export const NEXT_RUNS_BY_STATUS_INDEX = 'status_created';
export const STRANDED_RUNS_INDEX = 'queued_feature_waiting';
export const CLAIM_BY_PRIORITY_INDEX = 'claim_by_priority';

interface RunDocument extends HarnessRun {
  activeSessionKey: string | null;
  /** Admission-time grant; never part of HarnessRun or any response. */
  agentDefinition: AgentRunDefinition | null;
  /** The engine profile admission resolved (E4a); the claim pins it. Null: a loop run. */
  engineProfile: PinnedEngineProfile | null;
  /** DEEPROUTE: false when the org's automatic hand-off was off at admission; the claim pins it. Absent: on. */
  codeTaskAutoRoute?: false;
  /** GIT-FLOW (WP-26): the chat's repository on a run that hands work to an engine; the claim pins it. */
  repository?: SessionRepository;
  /** G-03..G-05: the run gates the turn asked for; the claim pins them as `RunSpec.gates`. */
  gates?: RunGates;
  /** WP-64: the run whose question this run answers, and the spending cap it carries on; the claim pins the cap. */
  answered?: AnsweredRunPin;
  modelAccess: PinnedModelAccess | null;
  via: RunVia;
  /** Where the run was started (FU4); absent: a child, or a run admitted before it was recorded. */
  surface?: RunSurface;
  /**
   * The pinned spec as canonical JSON: its keys contain dots (`x.pipeshub`,
   * `journal.durable`), and a string keeps the pinned bytes exact.
   */
  pinned: string | null;
  /** Whose turns the run may see (SS2); the claim pins it. Absent: a personal session. */
  audience?: RunAudience;
  /**
   * Next runs queued behind this run (SS2). Written in the enqueue's
   * transaction under a status filter, so the enqueue and the run's terminal
   * commit cannot miss each other.
   */
  nextRunsQueued?: number;
  /**
   * The ended turn's confidentiality label (P2.5a): its own work joined with
   * the runs it saw and its children, written with its transcript. Absent: an
   * unlabelled turn, shown to nobody but its actor.
   */
  label?: TurnLabel;
  /** The schedule firing the run is (P8a); absent: a person's turn. */
  trigger?: RunTrigger;
  /** Governed memory pinned at admission (P7.3); the claim pins it. Absent: none. */
  memory?: MemoryPin;
  /**
   * The run's pin (CPD-5 `on`) as canonical JSON: its effective config is
   * keyed by dotted dimension paths, which a string keeps exact.
   */
  resolved?: string;
}

type EventDocument = HarnessEvent;

interface ReceiptDocument {
  orgId: string;
  runId: string;
  commandId: string;
  payloadHash: string;
  receipt: unknown;
  createdAt: number;
}

interface EventCounterDocument {
  orgId: string;
  sessionId: string;
  nextEventSeq: number;
  /** Worker features the session's root stream needs to be folded (SS1). */
  journalFeatures?: string[];
  /** Everyone a run was admitted for in the session (SS2); absent before SS2: the owner only. */
  actors?: string[];
  /** The person's `/compact` waiting for the session's next run (SLASH2 C2); erased with the session. */
  compactRequest?: Record<string, unknown>;
}

interface AdmissionReceiptDocument {
  scopeKey: string;
  payloadHash: string;
  result: unknown;
  createdAt: number;
}

const immutableObjectRefSchema = new Schema(
  {
    objectId: { type: String, required: true },
    contentHash: { type: String, required: true },
    sizeBytes: { type: Number, required: true, min: 0 },
  },
  { _id: false },
);

const runSchema = new Schema<RunDocument>(
  {
    runId: { type: String, required: true },
    orgId: { type: String, required: true },
    sessionId: { type: String, required: true },
    actorId: { type: String, required: true },
    agentKey: { type: String },
    agentVersion: { type: Number },
    userMessageKey: { type: String, required: true },
    idempotencyKey: { type: String, required: true },
    payloadHash: { type: String, required: true },
    harnessMajor: { type: Number, enum: [...HARNESS_MAJORS], default: 1 },
    profile: { type: String, enum: ['inline', 'dispatched'], required: true },
    status: {
      type: String,
      enum: [...EXECUTION_STATUSES],
      required: true,
    },
    outcome: {
      type: String,
      enum: [...RUN_OUTCOMES, null],
      default: null,
    },
    // Which limit cut an answered run's work short (BUDGETEND); absent otherwise.
    truncatedBy: { type: String, enum: [...CUT_SHORT_REASONS] },
    revision: { type: Number, required: true },
    fence: { type: Number, required: true },
    attemptId: { type: String, default: null },
    workerId: { type: String, default: null },
    leaseExpiresAt: { type: Number, default: null },
    absoluteDeadlineAt: { type: Number, required: true },
    checkpoint: { type: immutableObjectRefSchema, default: null },
    attempts: { type: Number, default: 0 },
    claimedFrom: { type: String, default: null },
    notBefore: { type: Number, default: null },
    checkpointRef: { type: String, default: null },
    input: { type: Schema.Types.Mixed, default: null },
    inputSeq: { type: Number },
    pendingInputs: { type: [Number], default: undefined },
    openInteractions: { type: [String], default: undefined },
    requires: { type: Schema.Types.Mixed, default: undefined },
    attemptFeatures: { type: [String], default: undefined },
    waitingForWorker: { type: Schema.Types.Mixed, default: null },
    parent: { type: Schema.Types.Mixed, default: undefined },
    children: { type: Schema.Types.Mixed, default: undefined },
    suspendedFor: { type: String, default: undefined },
    workLeftMs: { type: Number, default: undefined },
    waitingChildren: { type: [String], default: undefined },
    priorityClass: { type: String, default: undefined },
    priorityRank: { type: Number, default: undefined },
    activeSessionKey: { type: String, default: null },
    agentDefinition: { type: Schema.Types.Mixed, default: null },
    engineProfile: { type: Schema.Types.Mixed, default: null },
    codeTaskAutoRoute: { type: Boolean, default: undefined },
    repository: { type: Schema.Types.Mixed, default: undefined },
    gates: { type: Schema.Types.Mixed, default: undefined },
    answered: { type: Schema.Types.Mixed, default: undefined },
    modelAccess: { type: Schema.Types.Mixed, default: null },
    via: {
      type: String,
      enum: ['session', 'pat', 'oauth_app', 'trigger', 'service'],
      default: 'session',
    },
    surface: {
      type: String,
      enum: ['web', 'slack', 'mcp'],
      default: undefined,
    },
    pinned: { type: String, default: null },
    resolved: { type: String, required: false },
    // C4.10: what Node applies of the pin (`interactions.waitS`, `recovery.autoResume`, `interactions.conversationGrants`).
    personWaitS: { type: Number, required: false },
    autoResume: { type: Boolean, required: false },
    conversationGrants: { type: Boolean, required: false },
    sessionRole: {
      type: String,
      enum: ['owner', 'collaborator'],
      default: undefined,
    },
    attribution: { type: Schema.Types.Mixed, default: undefined },
    audience: { type: Schema.Types.Mixed, default: undefined },
    memory: { type: Schema.Types.Mixed, default: undefined },
    nextRunsQueued: { type: Number, default: undefined },
    label: { type: Schema.Types.Mixed, default: undefined },
    /** The schedule firing a run is (P8a); store-only, pinned as `runKind: scheduled`. */
    trigger: { type: Schema.Types.Mixed, default: undefined },
    followups: { type: Schema.Types.Mixed, default: undefined },
    // G11: the operator stop that cancelled the run (scope, reason, DB time).
    operatorStop: { type: Schema.Types.Mixed, default: undefined },
    createdAt: { type: Number, required: true },
    updatedAt: { type: Number, required: true },
  },
  {
    collection: 'agentRuns',
    versionKey: false,
    autoIndex: false,
    minimize: false,
  },
);
runSchema.index({ orgId: 1, runId: 1 }, { unique: true });
runSchema.index(
  { activeSessionKey: 1 },
  {
    unique: true,
    partialFilterExpression: { activeSessionKey: { $type: 'string' } },
  },
);
runSchema.index({ status: 1, leaseExpiresAt: 1 });
// Claim-next: the queue is the queued v2 runs, oldest notBefore first (D20); the floor claim reads it so (G-45).
runSchema.index({ status: 1, harnessMajor: 1, notBefore: 1 });
// Claim-next by class (G-45): the highest class first, oldest notBefore within it. A run admitted before classes
// has no rank and sorts first, as every run did then.
runSchema.index(
  { status: 1, harnessMajor: 1, priorityRank: 1, notBefore: 1 },
  { name: CLAIM_BY_PRIORITY_INDEX },
);
runSchema.index({ status: 1, absoluteDeadlineAt: 1 });
// Admission counts active runs per org and per actor (quotas, P1.14).
runSchema.index({ orgId: 1, status: 1, actorId: 1 });
// The audit search (J-18): a person's or an agent's runs, newest first.
export const RUNS_BY_ACTOR_INDEX = 'orgId_1_actorId_1_createdAt_-1';
export const RUNS_BY_AGENT_INDEX = 'orgId_1_agentKey_1_createdAt_-1';
/** ADR-UI-01 D1: a person's runs newest first, ties by run id, so a page cursor is exact; with an agent, its own. */
export const RUNS_BY_ACTOR_CURSOR_INDEX =
  'orgId_1_actorId_1_createdAt_-1_runId_-1';
export const RUNS_BY_ACTOR_AGENT_INDEX =
  'orgId_1_actorId_1_agentKey_1_createdAt_-1_runId_-1';
runSchema.index(
  { orgId: 1, actorId: 1, createdAt: -1, runId: -1 },
  { name: RUNS_BY_ACTOR_CURSOR_INDEX },
);
runSchema.index(
  { orgId: 1, actorId: 1, agentKey: 1, createdAt: -1, runId: -1 },
  { name: RUNS_BY_ACTOR_AGENT_INDEX },
);
runSchema.index(
  { orgId: 1, actorId: 1, createdAt: -1 },
  { name: RUNS_BY_ACTOR_INDEX },
);
runSchema.index(
  { orgId: 1, agentKey: 1, createdAt: -1 },
  { name: RUNS_BY_AGENT_INDEX },
);
// The sweeper's stranded-run scan (DUR2): queued runs needing a feature, longest wait first.
runSchema.index(
  { 'requires.features': 1, 'waitingForWorker.since': 1 },
  { name: STRANDED_RUNS_INDEX, partialFilterExpression: { status: 'queued' } },
);
// Erasure and retention list a session's runs (E7).
runSchema.index({ orgId: 1, sessionId: 1 }, { name: RUNS_BY_SESSION_INDEX });
// A parent's open children (cancel cascade) and children left by an ended parent (P6.1).
runSchema.index(
  { orgId: 1, 'parent.runId': 1, status: 1 },
  {
    name: RUNS_BY_PARENT_INDEX,
    partialFilterExpression: { 'parent.runId': { $exists: true } },
  },
);
runSchema.index(
  { status: 1, createdAt: 1 },
  {
    name: CHILD_RUNS_BY_STATUS_INDEX,
    partialFilterExpression: { 'parent.runId': { $exists: true } },
  },
);

const eventSchema = new Schema<EventDocument>(
  {
    orgId: { type: String, required: true },
    sessionId: { type: String, required: true },
    runId: { type: String, required: true },
    eventSeq: { type: Number, required: true },
    commitSeq: { type: Number, required: true },
    type: { type: String, required: true },
    data: { type: Schema.Types.Mixed },
    createdAt: { type: Number, required: true },
  },
  {
    collection: 'agentEvents',
    versionKey: false,
    autoIndex: false,
    minimize: false,
  },
);
eventSchema.index({ orgId: 1, sessionId: 1, eventSeq: 1 }, { unique: true });
eventSchema.index({ orgId: 1, runId: 1, commitSeq: 1 });

const receiptSchema = new Schema<ReceiptDocument>(
  {
    orgId: { type: String, required: true },
    runId: { type: String, required: true },
    commandId: { type: String, required: true },
    payloadHash: { type: String, required: true },
    receipt: { type: Schema.Types.Mixed, required: true },
    createdAt: { type: Number, required: true },
  },
  {
    collection: 'agentCommandReceipts',
    versionKey: false,
    autoIndex: false,
    minimize: false,
  },
);
receiptSchema.index({ orgId: 1, runId: 1, commandId: 1 }, { unique: true });
// The receipt TTL pass reads the oldest first (T5).
receiptSchema.index({ createdAt: 1 }, { name: RECEIPTS_BY_AGE_INDEX });

const eventCounterSchema = new Schema<EventCounterDocument>(
  {
    orgId: { type: String, required: true },
    sessionId: { type: String, required: true },
    nextEventSeq: { type: Number, required: true, default: 0 },
    journalFeatures: { type: [String], default: undefined },
    actors: { type: [String], default: undefined },
    compactRequest: { type: Schema.Types.Mixed, default: undefined },
  },
  { collection: 'agentEventCounters', versionKey: false, autoIndex: false },
);
eventCounterSchema.index({ orgId: 1, sessionId: 1 }, { unique: true });

const admissionReceiptSchema = new Schema<AdmissionReceiptDocument>(
  {
    scopeKey: { type: String, required: true },
    payloadHash: { type: String, required: true },
    result: { type: Schema.Types.Mixed, required: true },
    createdAt: { type: Number, required: true },
  },
  {
    collection: 'agentAdmissionReceipts',
    versionKey: false,
    autoIndex: false,
    minimize: false,
  },
);
admissionReceiptSchema.index({ scopeKey: 1 }, { unique: true });
admissionReceiptSchema.index({ createdAt: 1 }, { name: RECEIPTS_BY_AGE_INDEX });
// A receipt holds the admitted run, its input included: erased with the session.
admissionReceiptSchema.index(
  { 'result.sessionId': 1 },
  { name: ADMISSION_RECEIPTS_BY_SESSION_INDEX },
);

const workspaceHeadSchema = new Schema<WorkspaceHeadDocument>(
  {
    orgId: { type: String, required: true },
    workspaceId: { type: String, required: true },
    revision: { type: Number, required: true },
    runId: { type: String, required: true },
    updatedAt: { type: Number, required: true },
    forkOf: {
      type: new Schema(
        {
          workspaceId: { type: String, required: true },
          revision: { type: Number, required: true },
        },
        { _id: false },
      ),
      required: false,
      default: undefined,
    },
    mergeBase: { type: Number, required: false },
    lastMerge: {
      type: new Schema(
        {
          key: { type: String, required: true },
          into: { type: String, required: true },
          base: { type: Number, required: true },
          theirs: { type: Number, required: true },
          ours: { type: Number, required: true },
          revision: { type: Number, default: null },
          status: { type: String, required: true },
        },
        { _id: false },
      ),
      required: false,
      default: undefined,
    },
  },
  { collection: 'agentWorkspaceHeads', versionKey: false, autoIndex: false },
);
workspaceHeadSchema.index({ orgId: 1, workspaceId: 1 }, { unique: true });

interface LeaseDocument {
  _id: string;
  holder: string;
  expiresAt: number;
}

const leaseSchema = new Schema<LeaseDocument>(
  {
    _id: { type: String, required: true },
    holder: { type: String, required: true },
    expiresAt: { type: Number, required: true },
  },
  { collection: 'agentHarnessLeases', versionKey: false, autoIndex: false },
);

interface AdmissionQuotaDocument {
  /** orgQuotaKey / userQuotaKey. */
  _id: string;
  windowStart: number;
  count: number;
  holds?: QuotaHold[];
}

/**
 * One fixed admission-rate window per org and per actor, with the admissions
 * in flight (`MongoAdmissionQuotaLedger`).
 */
const admissionQuotaSchema = new Schema<AdmissionQuotaDocument>(
  {
    _id: { type: String, required: true },
    windowStart: { type: Number, required: true },
    count: { type: Number, required: true },
    holds: { type: Schema.Types.Mixed, default: undefined },
  },
  { collection: 'agentAdmissionQuotas', versionKey: false, autoIndex: false },
);

interface NextRunDocument {
  _id: string;
  orgId: string;
  sessionId: string;
  actorId: string;
  runId: string;
  behindRunId: string;
  status: 'pending' | 'admitted' | 'refused';
  /** The admission as decided; dropped once settled (it holds the message). */
  command?: Record<string, unknown> | null;
  reason?: string;
  createdAt: number;
  settledAt?: number;
}

/**
 * Messages waiting for someone else's running task to end (SS2 `next_run`).
 * Erased with the session (`cancel_runs`, before anything could drain them).
 */
const nextRunSchema = new Schema<NextRunDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    sessionId: { type: String, required: true },
    actorId: { type: String, required: true },
    runId: { type: String, required: true },
    behindRunId: { type: String, required: true },
    status: {
      type: String,
      enum: ['pending', 'admitted', 'refused'],
      required: true,
    },
    command: { type: Schema.Types.Mixed, default: null },
    reason: { type: String, default: undefined },
    createdAt: { type: Number, required: true },
    settledAt: { type: Number, default: undefined },
  },
  {
    collection: 'agentNextRuns',
    versionKey: false,
    autoIndex: false,
    minimize: false,
  },
);
nextRunSchema.index(
  { orgId: 1, sessionId: 1, status: 1, createdAt: 1 },
  { name: NEXT_RUNS_BY_SESSION_INDEX },
);
nextRunSchema.index(
  { status: 1, createdAt: 1 },
  { name: NEXT_RUNS_BY_STATUS_INDEX },
);

const model = <T>(name: string, schema: Schema<T>): Model<T> =>
  harnessModel(name, schema);

export const AgentRunModel = model('AgentRun', runSchema);
export const AgentEventModel = model('AgentEvent', eventSchema);
export const AgentCommandReceiptModel = model(
  'AgentCommandReceipt',
  receiptSchema,
);
export const AgentEventCounterModel = model(
  'AgentEventCounter',
  eventCounterSchema,
);
export const AgentAdmissionReceiptModel = model(
  'AgentAdmissionReceipt',
  admissionReceiptSchema,
);
export const AgentHarnessLeaseModel = model('AgentHarnessLease', leaseSchema);
export const AgentWorkspaceHeadModel = model(
  'AgentWorkspaceHead',
  workspaceHeadSchema,
);
export const AgentAdmissionQuotaModel = model(
  'AgentAdmissionQuota',
  admissionQuotaSchema,
);
export const AgentNextRunModel = model('AgentNextRun', nextRunSchema);
