import { attributionAuditFields, RunAttribution } from './principal';
import type { KeywordSearchBudget } from '../../../libs/validators/keyword-search-budget';
import type { Followup } from './followups';
import type { RunTriggerContext } from './schedules';
import type { TurnRole } from './session-roles';
import type { JobBrief } from '../contracts/generated/ext/pipeshub/api/job-brief';
import type { RunGates } from './run-gates';
import type { UnlistedMcpReason } from './agent-definition';
import type {
  CutShortReason,
  InteractionChannel,
  InteractionDecision,
  SuspendReason,
  YieldReason,
} from '../contracts/generated/core/enums';
import {
  EXECUTION_STATUSES,
  TERMINAL_EXECUTION_STATUSES,
  type ExecutionStatus,
  type RunOutcome as DocumentOutcome,
} from '../contracts/generated/ext/pipeshub/enums';
import {
  assertChildResult,
  assertJoinWaits,
  type ChildResult,
} from './child-runs';
import { commandExpandedEvent, type RunCommand } from './commands';
import { DAY_MS } from './session-retention';
import { inlineContentRef } from './run-content';
import { canonicalJsonBytes } from '../contracts/canonical-json';
import { HarnessConflictError } from './errors';
import {
  JournalEvent,
  JournalEventInput,
  JournalAppend,
  PreparedJournalAppend,
  PreparedJournalEvent,
  validateJournalAppend,
} from './journal';
import { HarnessMajor } from './rollout';
import { principalRefFor, type PinnedRunSpec, type RunVia } from './run-spec';
import { assertInputTaken } from './steering';
import { assertAdvertisedEvents } from './recorded-run-fields';
import type { AdmittedRunKind } from './run-kind';
import type { PriorityClass } from './priority-class';
import { RETIRED, type OperatorStop } from './operator-controls';
import type {
  ClaimingWorker,
  RunRequirements,
  WaitingForWorker,
  WorkerFeature,
} from './worker-compat';

export const RUN_STATUSES = EXECUTION_STATUSES;
export type RunStatus = ExecutionStatus;
export type RunProfile = 'inline' | 'dispatched';
export type RunOutcome = DocumentOutcome | null;

export interface ImmutableObjectRef {
  objectId: string;
  contentHash: string;
  sizeBytes: number;
}

export interface HarnessRun {
  runId: string;
  orgId: string;
  sessionId: string;
  actorId: string;
  agentKey?: string;
  /** The published version of the agent the run ran (OWD-14): what its session records of the agent. */
  agentVersion?: number;
  userMessageKey: string;
  idempotencyKey: string;
  payloadHash: string;
  /** Pinned at admission so a later rollout change never moves the run. */
  harnessMajor: HarnessMajor;
  /** The region the run's data is stored in (OWD-12); absent on a run recorded before regions. */
  region?: string;
  profile: RunProfile;
  status: RunStatus;
  outcome: RunOutcome;
  /** Which limit cut a completed run's work short; it answered from what it had gathered (BUDGETEND). */
  truncatedBy?: CutShortReason;
  revision: number;
  fence: number;
  attemptId: string | null;
  workerId: string | null;
  leaseExpiresAt: number | null;
  /**
   * When the run ends if nothing moves it: its working deadline, or, while it
   * waits for a person (`waiting_approval`), the end of the time they have to
   * answer (FIX-P1-F3, `waitForPersonMs`); while it joins a child that waits
   * for a person, the end of that child's window plus its own working time left
   * (WAITCHILD).
   */
  absoluteDeadlineAt: number;
  /**
   * The working time the run had left when it, or a child it joins, began
   * waiting for a person; the answer (or the end of that wait) gives it back.
   * Absent or null: not held, or waiting since before it was recorded (the
   * deadline was then left as is).
   */
  workLeftMs?: number | null;
  /**
   * WAITCHILD: the open children whose working time is held for a person (the
   * child waits for one, or joins a child that does). A run joined on its
   * children holds its own working time while this is not empty.
   */
  waitingChildren?: string[];
  checkpoint: ImmutableObjectRef | null;
  /**
   * Claims since the run last left `running` cleanly (suspend, yield). The
   * worker quarantines a crash-looping run on it; absent on runs admitted
   * before it was recorded, which read as 0.
   */
  attempts?: number;
  /**
   * How the latest claim got the run: from the queue, or by taking over a
   * `running` run whose lease had expired before the sweeper re-queued it.
   * Decided in the claim's own update, on DB time.
   */
  claimedFrom?: ClaimedFrom | null;
  /** Earliest DB time a queued run may be claimed; absent or null: at once. */
  notBefore?: number | null;
  /** Harness major 2: the fold checkpoint a yield left for the next claim. */
  checkpointRef?: string | null;
  /** Trusted-worker input. It is never copied to broker messages or sandboxes. */
  input?: RunInputSnapshot | null;
  /** Steering (T2): the last `messageSeq` queued into the run; absent: none. */
  inputSeq?: number;
  /** Steering (T2): queued `messageSeq`s no commit has consumed yet. */
  pendingInputs?: number[];
  /**
   * Interactions the run requested and nobody resolved yet (P2.6c); absent on
   * runs that never requested one since it was recorded.
   */
  openInteractions?: string[];
  /** What a worker must support to take the run (RU-2); absent on runs admitted before T4. */
  requires?: RunRequirements;
  /** The features of the worker holding the current attempt (RU-2f). */
  attemptFeatures?: WorkerFeature[] | null;
  /** Queued and filtered from a claimer that could not run it (RU-2e); cleared by the claim that takes it. */
  waitingForWorker?: WaitingForWorker | null;
  /** A child run (P6.1): who spawned it. Absent on a root run. */
  parent?: ChildOf;
  /** A parent run's children: admitted, and not ended yet (the join waits on `open`). */
  children?: ChildCounts;
  /** Why the run last suspended; a `join` suspension is woken by its last child. */
  suspendedFor?: SuspendReason | null;
  /**
   * The actor's role when the run was admitted into a shared context (SS2);
   * absent in a personal session. A collaborator's run writes the
   * conversation it was admitted into although its actor does not own it.
   */
  sessionRole?: TurnRole;
  /**
   * Who the run acts as, who started it and on whose authority (OWD-2),
   * from verified auth at admission; a child carries its parent's. Absent on
   * runs admitted before it was recorded.
   */
  attribution?: RunAttribution;
  /** C4.10 `interactions.waitS` as the run's pin resolved it; absent: the deployment's wait. */
  personWaitS?: number;
  /** C4.10 `recovery.autoResume` off in the run's pin: a lost worker ends the run instead of resuming it. */
  autoResume?: false;
  /** C4.10 `interactions.conversationGrants` off in the run's pin: an approval is never held for the conversation. */
  conversationGrants?: false;
  /**
   * Suggested next questions, stored once after the run completed (UX5):
   * nothing may journal after a run's terminal event. Absent: none yet.
   */
  followups?: Followup[];
  /**
   * Which runs go first for a slot (G-45): from the run kind at admission, a child's from its parent, raised to
   * `approval_resume` once a person answers a lower-class run. `priorityRank` is its index, for the claim sort.
   * Absent on runs admitted before classes: read as interactive.
   */
  priorityClass?: PriorityClass;
  priorityRank?: number;
  /**
   * G-03..G-05: the gates the run is held to (the turn's, its agent's and, for an answer, the asking run's, WP-64).
   * The worker reads them from the pinned spec; absent: none.
   */
  gates?: RunGates;
  /**
   * G11 (ADR-OPS-01 D5): the operator stop that cancelled the run, kept on it so every later read (the run, a list,
   * a job's card after a reload) says who stopped it and why. Set by the first cancel that carries a stop; absent:
   * no operator stopped it.
   */
  operatorStop?: RunOperatorStop;
  createdAt: number;
  updatedAt: number;
}

/** An operator's stop as the run keeps it: the control's scope and reason, and when (DB time); never the note. */
export interface RunOperatorStop extends OperatorStop {
  at: number;
}

/** The stop a cancel leaves on `run`: its first one stands, so a repeat or a later stop never rewrites why. */
export const operatorStopAfterCancel = (
  run: Pick<HarnessRun, 'operatorStop'>,
  command: Pick<ControlRun, 'action' | 'stop'>,
  now: number,
): RunOperatorStop | undefined =>
  run.operatorStop ??
  (command.action === 'cancel' && command.stop !== undefined
    ? { scope: command.stop.scope, reason: command.stop.reason, at: now }
    : undefined);

export interface ChildOf {
  runId: string;
  /** The parent's tool invocation that asked for the child. */
  invocationId: string;
  /** 1 for a child of a root run (REPORT 6.9 caps it). */
  depth: number;
}

export interface ChildCounts {
  spawned: number;
  open: number;
}

export interface RunInputArtifact {
  artifactId: string;
  version?: number;
}

/**
 * The person's `/run <command>` (SLASH3 §3.5): the run makes no model call and runs the query's line (after
 * `SANDBOX_COMMAND_PREFIX`, so it stays sealed with the query) through the agent's own `run_code`, under the
 * agent's rules. Its turn never enters a later turn's context (SD9).
 */
export interface RunSandboxCommand {
  timeoutS: number;
}

/** A `/run` run's query: this prefix, then the line the worker runs. */
export const SANDBOX_COMMAND_PREFIX = '/run ';

/** The person's `/compact [focus]` a run takes at admission (SLASH2 C2): it compacts before its first turn. */
export interface RunCompact {
  focus?: string;
}

export interface RunInputSnapshot {
  query: string;
  recordIds: string[];
  filters: Record<string, unknown>;
  appliedFilters?: Record<string, unknown>;
  attachments: Array<Record<string, unknown>>;
  /**
   * Conversation artifacts the message names for an engine run to work on
   * (E1b): the worker resolves them as the actor and places them under
   * `in/`. Absent `version`: the artifact's latest when the turn starts.
   */
  inputArtifacts?: RunInputArtifact[];
  tools?: string[];
  agentCapabilities?: {
    internalSearch: boolean;
    webSearch: boolean;
    deepSearch?: boolean;
  };
  model: {
    modelKey?: string;
    modelName?: string;
    modelProvider?: string;
    modelFriendlyName?: string;
    reasoningEffort?: string;
    chatMode: string;
    /**
     * A model account the run reaches its model with (A1): the actor's own key
     * (`users/<actorId>/<provider>`) or an org cloud account (`cloud/<id>`).
     * Absent: the org's configured key. Engine runs only.
     */
    accountId?: string;
  };
  timezone?: string;
  currentTime?: string;
  /**
   * G-52: the person's locale (a BCP 47 tag from the request's `Accept-Language`), which sets the answer's language.
   * Set only while a live worker reads it; a claim sends it only for a run that requires `output_locale`.
   */
  locale?: string;
  /** Why a scheduled run exists (TR-03): staged for it at `/pipeshub/inputs/trigger.json`. */
  trigger?: RunTriggerContext;
  /** Server-derived transcript captured before the admitted user turn. */
  previousConversations?: Array<Record<string, unknown>>;
  /**
   * The person behind a service credential (a Slack user), for the model's
   * context only, as legacy's `callerEmail`/`callerDisplayName`: the email
   * comes from the verified token.
   */
  caller?: { email?: string; displayName?: string };
  /** The /command `query` was expanded from (SLASH1); `query` is already the expanded text. */
  command?: RunCommand;
  /** The person's `/compact` this run took from its session (SLASH2 C2); never anything the request body sent. */
  compact?: RunCompact;
  /** A `/run` (SLASH3): set only by the command route, never by a turn's body. */
  sandboxCommand?: RunSandboxCommand;
  /** The turn asked for today's loop's measurement frames (`retrieval_context`, `run_usage`, SYNC2-F1). */
  includeRetrievalContext?: boolean;
  /**
   * How long each knowledge search waits for its keyword matches (the chat routes' `keywordSearchBudget`). Set only
   * while a live worker reads it; a claim sends it only for a run that requires `keyword_search_budget`.
   */
  keywordSearchBudget?: KeywordSearchBudget;
  /**
   * A job's task in its parts, as its parent's worker composed it (BGUI-10): what its brief shows a person. Set only
   * by a child's admission; the worker that runs the job reads `query`, and is never sent this (`workerInput`).
   */
  task?: RunTaskParts;
  /**
   * Where the run's end is posted when the stream that asked for it ended first (ADR-OWD-09 D-SLACK-ASK): the Slack
   * thread a Slack turn came from. Set only by the Slack route, its bot from the verified token; Node's alone, never
   * sent to a worker (`workerInput`).
   */
  reply?: RunReplyTarget;
}

/** A Slack thread: the bot configuration that answers it, and the thread's channel and root message. */
export interface RunReplyTarget {
  slack: { botId: string; channel: string; threadTs: string };
}

/** A job's task in its parts (`job-brief.schema.json` JobTask). */
export type RunTaskParts = NonNullable<JobBrief['task']>;

export interface HarnessEvent {
  orgId: string;
  sessionId: string;
  runId: string;
  eventSeq: number;
  commitSeq: number;
  type: string;
  data: unknown;
  createdAt: number;
}

export interface DispatchCommand {
  commandId: string;
  runId: string;
  kind: 'run_ready' | 'run_resume' | 'run_cancel';
}

/** A dispatch command as written to the shared outbox. */
export interface DispatchRecord extends DispatchCommand {
  orgId: string;
  createdAt: number;
}

export interface ClaimRun {
  orgId: string;
  runId: string;
  workerId: string;
  attemptId: string;
  leaseDurationMs: number;
  /**
   * What the claiming worker can run (RU-2). Every worker claim carries one
   * (an unadvertised worker reads as `UNADVERTISED_WORKER`); absent only for
   * Node's own callers, which are not filtered.
   */
  worker?: ClaimingWorker;
}

/**
 * Who claims from the queue. The inline claimer (the query service that
 * admitted the run) takes inline runs at once; dispatched workers take
 * dispatched runs, and inline runs nobody claimed within `T_CLAIM_MS`.
 */
export type ClaimRole = 'inline' | 'dispatched';

/** The Mongo claim is the queue (D20): the next claimable run, if any. */
export interface ClaimNextRun {
  role: ClaimRole;
  workerId: string;
  attemptId: string;
  leaseDurationMs: number;
  worker?: ClaimingWorker;
  /** A full worker making room by preemption (G-45): only a run of a strictly higher class than this. */
  aboveClass?: PriorityClass;
}

/** A queued candidate for claim-next; claiming it may still lose a race. */
export interface ClaimCandidate {
  orgId: string;
  runId: string;
}

/** Gives a claimed attempt back to the queue without a transition. */
export interface ReleaseAttempt {
  orgId: string;
  runId: string;
  workerId: string;
  attemptId: string;
  fence: number;
  reason: string;
}

export interface RenewAttempt {
  orgId: string;
  runId: string;
  workerId: string;
  attemptId: string;
  fence: number;
  leaseDurationMs: number;
}

export interface CommitTransition {
  commandId: string;
  orgId: string;
  runId: string;
  workerId: string;
  attemptId: string;
  fence: number;
  expectedRevision: number;
  status: RunStatus;
  outcome?: Exclude<RunOutcome, null>;
  checkpoint?: ImmutableObjectRef;
  events: Array<{ type: string; data: unknown }>;
  outbox: DispatchCommand[];
}

/** Durable v1 wire shape; still accepted and normalized to a CommitBatch. */
export type LegacyEvent = CommitTransition['events'][number];

export type { SuspendReason, YieldReason };

/** Written by Node when a claim-checked payload could not be stored or read (REPORT 6.17); a worker never yields for it. */
export const NODE_AUTHORED_YIELD_REASONS = [
  'payload_store_unavailable',
] as const satisfies readonly YieldReason[];

/**
 * Harness major 2: which `run.*` event Node journals for a worker transition,
 * beyond what the status alone says (REPORT 6.1.3).
 */
export type TransitionDetail =
  | { kind: 'suspend'; reason: SuspendReason; interactionId?: string }
  | {
      kind: 'yield';
      reason: YieldReason;
      /** RFC 3339 UTC; the run is claimable again from then (DB time). */
      notBefore: string;
      checkpointRef?: string;
    }
  | { kind: 'quarantine'; attempts: number; lastErrorType?: string }
  | {
      kind: 'fail';
      message?: string;
      servers?: string[];
      memoryLimitMb?: number;
    };

export interface RunTransition {
  /** Required on v1; a v2 worker is fenced by its attempt and may omit it. */
  expectedRevision?: number;
  status: RunStatus;
  outcome?: Exclude<RunOutcome, null>;
  checkpoint?: ImmutableObjectRef;
  /** Recorded in `run.failed{errorType}` on a v2 run. */
  errorType?: string;
  detail?: TransitionDetail;
  /** A completion that ends with queued input untaken (no turn left; T2). */
  leavesInput?: boolean;
  /** A child run's completion: its result document and label (P6.1). */
  childResult?: ChildResult;
  /** An answered completion whose work a limit cut short (BUDGETEND): journaled on `run.completed`. */
  truncatedBy?: CutShortReason;
}

/** Moves a workspace HEAD in the same transaction as its workspace.committed. */
export interface WorkspaceHeadCas {
  workspaceId: string;
  expectedRevision: number;
  revision: number;
}

/**
 * One fenced worker commit (REPORT 6.1.4 transport): typed journal events,
 * durable-v1 events, an optional run transition, workspace HEAD CAS and
 * outbox, all in one transaction under one deterministic commandId.
 */
export interface CommitBatch {
  commandId: string;
  orgId: string;
  runId: string;
  workerId: string;
  attemptId: string;
  fence: number;
  events: JournalEventInput[];
  legacyEvents: LegacyEvent[];
  transition?: RunTransition;
  workspaceHeadCAS?: WorkspaceHeadCas;
  outbox: DispatchCommand[];
}

export interface JournalRange {
  streamId: string;
  firstSeq: number;
  lastSeq: number;
}

export interface CommitReceipt {
  commandId: string;
  run: HarnessRun;
  /** Durable v1 `agentEvents` range. */
  firstEventSeq: number | null;
  lastEventSeq: number | null;
  /** Typed journal range; absent on control receipts. */
  journal?: JournalRange | null;
  /** Set on the returned copy when a retried commandId replayed; never stored. */
  replayed?: true;
}

/** A claim, plus where each stream's journal ends for the worker's fold [R-4]. */
export type ClaimedFrom = 'queued' | 'expired_lease';

export interface ClaimedRun {
  run: HarnessRun;
  journal: { lastSeq: Record<string, number> };
  /** Harness major 2: the spec pinned on the first claim, for every claim. */
  pinned?: PinnedRunSpec;
}

/**
 * A spec to pin if the run is still unpinned when the claim transaction runs.
 * Resolved and claim-checked before the transaction; a claim that finds a
 * pin already there keeps it and drops this one.
 */
export interface ClaimPinning {
  pinned: PinnedRunSpec;
  /** `run.spec_pinned`, validated and possibly claim-checked. */
  specEvent: PreparedJournalEvent;
}

export type { InteractionChannel, InteractionDecision };

/** How a v2 run's open interaction was answered; journaled before it resumes. */
/** A person's approval that holds for the rest of the conversation, for one tool and destination (UX1). */
export type InteractionScope = 'conversation';

export interface InteractionResolution {
  interactionId: string;
  decision: InteractionDecision;
  channel: InteractionChannel;
  /** Only with `approved`, and only when the worker offered it (`interaction.explained`). */
  scope?: InteractionScope;
  /** AP-05: why the person declined, journaled as the resolution's `answerRef`; only with `denied`. */
  reason?: string;
  /**
   * AP-06: the arguments the person approved in place of the call's own, journaled as the resolution's
   * `answerRef` (canonical JSON, inline, with its hash); only with `approved`, and only where the worker offered it.
   */
  edits?: Record<string, unknown>;
}

/** The largest edit a person may approve, as canonical JSON; the worker offers editing only below it. */
export const MAX_EDITED_ARGS_BYTES = 64 * 1024;

/** The answer an approval's edit is journaled as: canonical JSON bytes, so its hash is the edit's own digest. */
export const editedArgsRef = (
  edits: Record<string, unknown>,
): { ref: string; hash: string; size: number } =>
  inlineContentRef(canonicalJsonBytes(edits));

export interface ControlRun {
  commandId: string;
  dispatchCommandId: string;
  orgId: string;
  runId: string;
  expectedRevision: number;
  /**
   * `resolve`: an interaction decided, whatever the run's state (P2.6c `resolveActive`). `suspend`: an operator
   * retired the run's engine version (ADR-OPS-01 D6): a queued or running run is paused, its attempt taken back as a
   * lapsed lease's is, until a `resume` with `cause` `version_available`.
   */
  action: 'resume' | 'cancel' | 'resolve' | 'suspend';
  /** Principal reference of whoever asked; journaled on a v2 run. */
  by?: string;
  /** `cancel` asked by an operator control (ADR-OPS-01): journaled on `run.cancel_requested` so the person is told why. */
  stop?: OperatorStop;
  /** `resume` by an operator control, with no interaction to resolve (ADR-OPS-01 D6). */
  cause?: 'version_available';
  /** `suspend`: when the held run ends if nothing resumes it (DB time ms); its working time left is kept. */
  holdUntil?: number;
  /** Harness major 2: a resume resolves the interaction it waits on. */
  resolution?: InteractionResolution;
  /** DECIDE409: several interactions resolved in one transaction ("Allow all"); replaces `resolution`. */
  resolutions?: readonly InteractionResolution[];
  /**
   * DECIDE409: a `resolve` prepared against this status. On a run that tracks its open interactions the store
   * then checks the status and that each interaction is still open, not the revision, so decisions on different
   * interactions, and the worker's own commits, never refuse each other.
   */
  expectedStatus?: RunStatus;
}

/** What a control command resolves, one interaction or a batch. */
/** Most characters of a deny reason the run is told (the worker bounds what the model reads the same way). */
export const MAX_DENY_REASON_CHARS = 1000;

/**
 * `interaction.resolved`'s data; a denial's reason rides inline as `answerRef`, so the worker reads it without a
 * content store and the engine or loop is told why (AP-05).
 */
export function resolvedData(
  resolution: InteractionResolution,
  by: string,
): Record<string, unknown> {
  const reason =
    resolution.decision === 'denied' ? resolution.reason?.trim() : undefined;
  const edits =
    resolution.decision === 'approved' ? resolution.edits : undefined;
  return {
    interactionId: resolution.interactionId,
    decision: resolution.decision,
    by,
    channel: resolution.channel,
    ...(reason !== undefined && reason.length > 0
      ? { answerRef: inlineContentRef(Buffer.from(reason, 'utf8')) }
      : {}),
    ...(edits !== undefined ? { answerRef: editedArgsRef(edits) } : {}),
  };
}

export const controlResolutions = (
  command: Pick<ControlRun, 'resolution' | 'resolutions'>,
): readonly InteractionResolution[] =>
  command.resolutions ??
  (command.resolution !== undefined ? [command.resolution] : []);

/** The store's clock: DB time for Mongo; injectable for the in-memory store. */
export interface StoreClock {
  now(): number;
}

export const systemStoreClock: StoreClock = { now: () => Date.now() };

export const TERMINAL_RUN_STATUSES: ReadonlySet<RunStatus> = new Set(
  TERMINAL_EXECUTION_STATUSES,
);

/**
 * A commit that ends a run stopped (`cancelled`) still ends it when its transcript has no conversation to go to (the
 * person deleted the chat, ADR-RUN-05 D4): the transcript is dropped, as erasure would drop it. Any other ending of a
 * run whose conversation was deleted is refused naming the cause (QA21-F4).
 */
export const endsWithoutTranscript = (status: RunStatus | undefined): boolean =>
  status === 'cancelled';

export const WORKER_OWNED_RUN_STATUSES: ReadonlySet<RunStatus> = new Set([
  'running',
  'cancelling',
]);

/**
 * An attempt other than `holder` holds the run on a lease that has not lapsed by `now`. A store-side failure
 * (`ExecutionStore.failRun`) refuses such a run: it would clear the lease under a worker still in its attempt, which
 * a claim never does. Once the lease lapses the run is lost and may be failed.
 */
export const heldByAnotherAttempt = (
  run: Pick<HarnessRun, 'status' | 'leaseExpiresAt' | 'attemptId'>,
  now: number,
  holder: string | null,
): boolean =>
  WORKER_OWNED_RUN_STATUSES.has(run.status) &&
  run.leaseExpiresAt !== null &&
  run.leaseExpiresAt > now &&
  run.attemptId !== holder;

/**
 * How long an inline run waits for the process that admitted it to claim it
 * before the sweeper hands it to dispatched workers [R-11].
 */
/**
 * An interactive run nobody claimed, and that nothing changed, this long fails
 * `not_started` (UX3, R1 F4): long enough to outlast a worker's rolling
 * restart (40 s drain, A-31), far short of the run's hour.
 */
export const INTERACTIVE_CLAIM_DEADLINE_MS = 90_000;
/** The failure a run gets when no worker ever started it (UX3). */
export const NOT_STARTED = 'not_started';
export const T_CLAIM_MS = 5_000;

/** `HARNESS_WAIT_FOR_PERSON_DAYS`: how long a run waiting for a person stays answerable. */
export const WAIT_FOR_PERSON_ENV = 'HARNESS_WAIT_FOR_PERSON_DAYS';
export const DEFAULT_WAIT_FOR_PERSON_DAYS = 7;
const MAX_WAIT_FOR_PERSON_DAYS = 365;

/**
 * How long a run waiting for a person's decision stays answerable (FIX-P1-F3).
 * Before, the wait counted against the run's working deadline (an hour for a
 * chat), so an approval left over lunch failed the run. Fractions of a day are
 * allowed; a value that is not a number of days in (0, 365] is refused at boot.
 */
export function waitForPersonMs(
  env: Readonly<Record<string, string | undefined>> = process.env,
): number {
  const raw = env[WAIT_FOR_PERSON_ENV]?.trim();
  if (raw === undefined || raw === '') {
    return DEFAULT_WAIT_FOR_PERSON_DAYS * DAY_MS;
  }
  const days = Number(raw);
  if (!Number.isFinite(days) || days <= 0 || days > MAX_WAIT_FOR_PERSON_DAYS) {
    throw new Error(
      `${WAIT_FOR_PERSON_ENV} must be a number of days above 0 and at most ${String(MAX_WAIT_FOR_PERSON_DAYS)}, got "${raw}"`,
    );
  }
  return Math.round(days * DAY_MS);
}

/**
 * A run waits for a person when it is suspended for a decision: an approval or
 * an answer (both suspend it as `waiting_approval`). Join, timer and event
 * waits are the run's own and keep counting.
 */
export const waitsForPerson = (status: RunStatus): boolean =>
  status === 'waiting_approval';

/**
 * The move into a person's wait, where the working deadline is held: a run of a
 * chat, root or child (WAITCHILD: a child holds its time as a root does, and the
 * parents joined on it hold theirs with it, `ancestorHeld`). A scheduled
 * firing's whole tree keeps the schedule's own deadline, which the approval
 * notice names (AP-1): `scheduled` is whether the run's root is a firing.
 */
export const entersPersonWait = (
  run: Pick<HarnessRun, 'status'>,
  to: RunStatus,
  scheduled: boolean,
): boolean => waitsForPerson(to) && !waitsForPerson(run.status) && !scheduled;

/**
 * WP56-OUTBOX: a commit moves a v2 run into a person's wait, scheduled or not: the commit writes the wait's approver
 * notices, as `ExecutionService.onRunWaiting` tells its listeners of the same moves.
 */
export const startsWaitingForPerson = (
  run: Pick<HarnessRun, 'status' | 'harnessMajor'>,
  to: RunStatus,
): boolean =>
  run.harnessMajor === 2 && waitsForPerson(to) && !waitsForPerson(run.status);

/** Suspended until its open children end (`join`). */
export const joinsChildren = (
  run: Pick<HarnessRun, 'status' | 'suspendedFor'>,
): boolean => run.status === 'paused' && run.suspendedFor === 'join';

/** Suspended because an operator retired its engine version (ADR-OPS-01 D6): its working time waits with it. */
export const retiredHold = (
  run: Pick<HarnessRun, 'status' | 'suspendedFor'>,
): boolean => run.status === 'paused' && run.suspendedFor === RETIRED;

/**
 * The working time a run gets back when its wait for a person ends; null when
 * it is not held. Held: it waits for a person itself, or joins a child that
 * does (WAITCHILD).
 */
export const heldWorkLeft = (
  run: Pick<HarnessRun, 'status' | 'suspendedFor' | 'workLeftMs'>,
): number | null =>
  (waitsForPerson(run.status) || joinsChildren(run) || retiredHold(run)) &&
  typeof run.workLeftMs === 'number'
    ? run.workLeftMs
    : null;

type HeldFields = Pick<HarnessRun, 'absoluteDeadlineAt' | 'workLeftMs'>;
type HoldSubject = Pick<
  HarnessRun,
  'status' | 'suspendedFor' | 'workLeftMs' | 'absoluteDeadlineAt'
>;

/**
 * The deadline fields of a run that begins waiting for a person at `now`: the
 * working time left is kept, and the run stays answerable for the window (never
 * less than its working deadline gave it).
 */
export const personWaitDeadline = (
  run: Pick<HarnessRun, 'absoluteDeadlineAt'>,
  now: number,
  windowMs: number,
): HeldFields => ({
  absoluteDeadlineAt: Math.max(run.absoluteDeadlineAt, now + windowMs),
  workLeftMs: Math.max(0, run.absoluteDeadlineAt - now),
});

/**
 * The deadline fields of a joined run whose child, now waiting for a person,
 * ends at `childDeadline` if nobody answers (WAITCHILD). The run keeps the
 * working time it has left (or had, when already held) and outlives the child
 * by that much: the child's window ends first, and the run still has its time to
 * read what the child did. Never less than its working deadline gave it.
 */
export function heldForChild(
  run: HoldSubject,
  childDeadline: number,
  now: number,
): HeldFields {
  const left = heldWorkLeft(run) ?? Math.max(0, run.absoluteDeadlineAt - now);
  return {
    workLeftMs: left,
    absoluteDeadlineAt: Math.max(run.absoluteDeadlineAt, childDeadline + left),
  };
}

/** What one step up a person-wait's ancestry writes, and whether the next ancestor moves too. */
export interface AncestorStep {
  fields: Partial<HeldFields> & { waitingChildren: string[] };
  continues: boolean;
}

/**
 * A child of `parent` began holding its time for a person, its deadline now
 * `childDeadline` (WAITCHILD). The parent records it; a parent joined on its
 * children holds its own time with it, and its own parent sees it held in turn.
 * A parent still working keeps counting (its child is bounded by it), and one
 * already past its deadline is the sweeper's.
 */
export function ancestorHeld(
  parent: HoldSubject & Pick<HarnessRun, 'waitingChildren'>,
  childRunId: string,
  childDeadline: number,
  now: number,
): AncestorStep {
  const known = parent.waitingChildren ?? [];
  const waitingChildren = known.includes(childRunId)
    ? [...known]
    : [...known, childRunId];
  if (
    !joinsChildren(parent) ||
    (heldWorkLeft(parent) === null && parent.absoluteDeadlineAt <= now)
  ) {
    return { fields: { waitingChildren }, continues: false };
  }
  return {
    fields: { waitingChildren, ...heldForChild(parent, childDeadline, now) },
    continues: true,
  };
}

/**
 * A child of `parent` stopped holding its time for a person: answered, ended,
 * or resumed (WAITCHILD). A held parent with no other child waiting gets its
 * working time back from `now`, as does one its last child wakes (`wakes`); its
 * own parent then sees it released in turn.
 */
export function ancestorReleased(
  parent: HoldSubject & Pick<HarnessRun, 'waitingChildren'>,
  childRunId: string,
  now: number,
  wakes = false,
): AncestorStep {
  const waitingChildren = (parent.waitingChildren ?? []).filter(
    (id) => id !== childRunId,
  );
  // A parent waiting for a person itself keeps its own hold until that answer.
  const held = joinsChildren(parent) ? heldWorkLeft(parent) : null;
  if (held === null || (waitingChildren.length > 0 && !wakes)) {
    return { fields: { waitingChildren }, continues: false };
  }
  return {
    fields: {
      waitingChildren,
      absoluteDeadlineAt: now + held,
      workLeftMs: null,
    },
    continues: true,
  };
}

/**
 * The deadline fields of a run suspending on `join` at `now` (WAITCHILD): held
 * when children already wait for a person (`childDeadlines`: those of its
 * `waitingChildren`), its working time counting otherwise.
 */
export function joinDeadline(
  run: Pick<HarnessRun, 'absoluteDeadlineAt'>,
  childDeadlines: readonly number[],
  now: number,
): HeldFields {
  if (childDeadlines.length === 0) {
    return { absoluteDeadlineAt: run.absoluteDeadlineAt, workLeftMs: null };
  }
  return heldForChild(
    {
      status: 'paused',
      suspendedFor: 'join',
      workLeftMs: null,
      absoluteDeadlineAt: run.absoluteDeadlineAt,
    },
    Math.max(...childDeadlines),
    now,
  );
}

/** Non-terminal statuses without a worker lease; only the deadline ends them. */
export const IDLE_RUN_STATUSES: ReadonlySet<RunStatus> = new Set([
  'queued',
  'waiting_approval',
  'paused',
  'recovering',
  'needs_reconciliation',
]);

const WORKER_TRANSITIONS: Readonly<Record<RunStatus, ReadonlySet<RunStatus>>> =
  {
    queued: new Set(),
    running: new Set([
      // Only a v2 yield (validateCommitBatch).
      'queued',
      'running',
      'waiting_approval',
      'paused',
      'recovering',
      'needs_reconciliation',
      'cancelling',
      'completed',
      'failed',
      'cancelled',
    ]),
    waiting_approval: new Set(['queued', 'cancelled']),
    paused: new Set(['queued', 'cancelled']),
    recovering: new Set([
      'queued',
      'needs_reconciliation',
      'failed',
      'cancelled',
    ]),
    needs_reconciliation: new Set(['queued', 'failed', 'cancelled']),
    cancelling: new Set(['cancelled', 'failed']),
    completed: new Set(),
    failed: new Set(),
    cancelled: new Set(),
  };

export function canWorkerTransition(from: RunStatus, to: RunStatus): boolean {
  return WORKER_TRANSITIONS[from].has(to);
}

export interface AttemptIdentity {
  workerId: string;
  attemptId: string;
  fence: number;
}

/** The attempt holds the run's lease. */
export const attemptOwnsRun = (
  run: HarnessRun,
  attempt: AttemptIdentity,
): boolean =>
  WORKER_OWNED_RUN_STATUSES.has(run.status) &&
  run.workerId === attempt.workerId &&
  run.attemptId === attempt.attemptId &&
  run.fence === attempt.fence;

/**
 * The latest attempt keeps reading after it suspended, yielded or ended
 * (attempt cleared, fence unchanged) until its token expires; a later claim
 * supersedes it.
 */
export const attemptMayReadRun = (
  run: HarnessRun,
  attempt: AttemptIdentity,
): boolean =>
  attemptOwnsRun(run, attempt) ||
  (run.fence === attempt.fence &&
    !WORKER_OWNED_RUN_STATUSES.has(run.status) &&
    run.attemptId === null);

/** A store may hold `parent: null` on a root run. */
export const childOf = (run: { parent?: ChildOf | null }): ChildOf | null =>
  run.parent ?? null;

/** The root run journals to the session stream, a child run to its own (REPORT 6.1.2). */
export const journalStreamId = (
  run: Pick<HarnessRun, 'sessionId' | 'runId'> & { parent?: ChildOf | null },
): string => (childOf(run) !== null ? run.runId : run.sessionId);

export function commitBatchFromTransition(
  command: CommitTransition,
): CommitBatch {
  return {
    commandId: command.commandId,
    orgId: command.orgId,
    runId: command.runId,
    workerId: command.workerId,
    attemptId: command.attemptId,
    fence: command.fence,
    events: [],
    legacyEvents: command.events,
    transition: {
      expectedRevision: command.expectedRevision,
      status: command.status,
      ...(command.outcome !== undefined ? { outcome: command.outcome } : {}),
      ...(command.checkpoint !== undefined
        ? { checkpoint: command.checkpoint }
        : {}),
    },
    outbox: command.outbox,
  };
}

/** Rules that need the run; the store re-checks ownership inside its transaction. */
export function validateCommitBatch(run: HarnessRun, batch: CommitBatch): void {
  assertAdvertisedEvents(batch);
  if (batch.legacyEvents.length > 0 && run.harnessMajor !== 1) {
    throw new HarnessConflictError(
      'INVALID_COMMIT',
      'Durable v1 events are accepted only on harness major 1 runs',
    );
  }
  const head = batch.workspaceHeadCAS;
  if (head !== undefined) {
    if (head.revision <= head.expectedRevision) {
      throw new HarnessConflictError(
        'INVALID_COMMIT',
        'A workspace HEAD can only move forward',
      );
    }
    const recorded = batch.events.some(
      (event) =>
        event.type === 'workspace.committed' &&
        event.data['workspaceId'] === head.workspaceId &&
        event.data['revision'] === head.revision,
    );
    if (!recorded) {
      throw new HarnessConflictError(
        'INVALID_COMMIT',
        'A workspace HEAD move needs its workspace.committed event in the same commit',
      );
    }
  }
  const transition = batch.transition;
  if (transition === undefined) return;
  if (!canWorkerTransition(run.status, transition.status)) {
    throw new HarnessConflictError(
      'INVALID_TRANSITION',
      `Cannot transition a run from ${run.status} to ${transition.status}`,
    );
  }
  if (transition.status === 'completed' && transition.outcome === undefined) {
    throw new HarnessConflictError(
      'INVALID_TRANSITION',
      'Completed runs require an outcome',
    );
  }
  if (
    transition.truncatedBy !== undefined &&
    (transition.status !== 'completed' || transition.outcome !== 'answer')
  ) {
    throw new HarnessConflictError(
      'INVALID_TRANSITION',
      'Only an answered completion is cut short by a limit',
    );
  }
  validateTransitionDetail(run, transition);
  assertInputTaken(run, batch);
  assertChildResult(run, transition);
  assertJoinWaits(run, transition);
}

/**
 * The statuses a harness v2 worker moves a run to: each journals its `run.*`
 * event (transitionJournalEvents), so the fold sees what the store holds.
 * `running`, `recovering`, `needs_reconciliation` and `cancelling` have no
 * event, and only a v1 worker or Node itself reaches them (QA13-F2).
 */
const V2_TRANSITION_STATUSES: ReadonlySet<RunStatus> = new Set([
  'queued',
  'waiting_approval',
  'paused',
  'completed',
  'failed',
  'cancelled',
]);

const DETAIL_FOR_STATUS: Partial<
  Record<RunStatus, ReadonlyArray<TransitionDetail['kind']>>
> = {
  queued: ['yield'],
  waiting_approval: ['suspend'],
  paused: ['suspend'],
  failed: ['quarantine', 'fail'],
};

function invalidTransition(message: string): HarnessConflictError {
  return new HarnessConflictError('INVALID_TRANSITION', message);
}

/** A v2 transition names its `run.*` event; a v1 one never carries a detail. */
function validateTransitionDetail(
  run: HarnessRun,
  transition: RunTransition,
): void {
  const detail = transition.detail;
  if (run.harnessMajor !== 2) {
    if (detail !== undefined || transition.status === 'queued') {
      throw invalidTransition('Only harness v2 transitions carry a detail');
    }
    if (transition.expectedRevision === undefined) {
      throw invalidTransition('A v1 transition needs its expected revision');
    }
    return;
  }
  if (!V2_TRANSITION_STATUSES.has(transition.status)) {
    throw invalidTransition(
      `A harness v2 run cannot move to ${transition.status}: it has no journal event`,
    );
  }
  const allowed = DETAIL_FOR_STATUS[transition.status] ?? [];
  if (detail !== undefined && !allowed.includes(detail.kind)) {
    throw invalidTransition(
      `A ${detail.kind} detail does not fit a ${transition.status} transition`,
    );
  }
  const required = transition.status === 'failed' ? [] : allowed;
  if (required.length > 0 && detail === undefined) {
    throw invalidTransition(
      `A ${transition.status} transition needs a ${required.join('/')} detail`,
    );
  }
  if (detail?.kind === 'yield' && Number.isNaN(Date.parse(detail.notBefore))) {
    throw invalidTransition('A yield needs an RFC 3339 notBefore');
  }
  if (detail?.kind === 'suspend' && transition.status === 'waiting_approval') {
    if (detail.reason !== 'approval') {
      throw invalidTransition('Only an approval suspension waits for approval');
    }
  } else if (detail?.kind === 'suspend' && detail.reason === 'approval') {
    throw invalidTransition('An approval suspension waits for approval');
  }
}

/** Run fields a v2 transition sets besides its status (DB time applied by the store). */
export interface TransitionEffects {
  attempts?: 0;
  /** Epoch ms the worker asked for; the store never sets it before now. */
  notBefore?: number;
  checkpointRef?: string | null;
  profile?: RunProfile;
  /** What the run waits on now; every v2 transition sets it. */
  suspendedFor?: SuspendReason | null;
}

export function transitionEffects(
  run: Pick<HarnessRun, 'harnessMajor'>,
  transition: RunTransition | undefined,
): TransitionEffects {
  const detail = transition?.detail;
  if (run.harnessMajor !== 2 || transition === undefined) return {};
  if (detail === undefined) return { suspendedFor: null };
  if (detail.kind === 'suspend') {
    return { attempts: 0, suspendedFor: detail.reason };
  }
  if (detail.kind !== 'yield') return { suspendedFor: null };
  return {
    suspendedFor: null,
    attempts: 0,
    notBefore: Date.parse(detail.notBefore),
    checkpointRef: detail.checkpointRef ?? null,
    ...(detail.reason === 'promotion' ? { profile: 'dispatched' } : {}),
  };
}

/** A yielded run is queued again; its wake hint rides in the same commit. */
export function transitionOutbox(
  run: Pick<HarnessRun, 'harnessMajor' | 'runId' | 'fence'>,
  transition: RunTransition | undefined,
): DispatchCommand[] {
  if (run.harnessMajor !== 2 || transition?.detail?.kind !== 'yield') return [];
  return [
    {
      commandId: `yield:${run.runId}:${String(run.fence)}`,
      runId: run.runId,
      kind: 'run_resume',
    },
  ];
}

/**
 * run.* events are Node's to write (writer allowlist): on a v2 run a terminal
 * transition is journaled here, in the worker's commit transaction.
 */
export const nodeRunEvent = (
  type: string,
  data: Record<string, unknown>,
): JournalEventInput => ({
  type,
  schemaVersion: 1,
  critical: true,
  visibility: 'owner',
  data,
});

/** Admission names the principal (I3) and how it reached the run (audited). */
export const runAdmittedEvent = (
  principalRef: string,
  via: RunVia,
  sessionRole?: TurnRole,
  runKind: AdmittedRunKind = 'interactive',
  attribution?: RunAttribution,
): JournalEventInput => ({
  ...nodeRunEvent('run.admitted', {
    runKind,
    principalRef,
    via,
    ...(sessionRole !== undefined ? { sessionRole } : {}),
  }),
  ...(attribution !== undefined
    ? { auditFields: attributionAuditFields(attribution) }
    : {}),
});

/**
 * A small Node append a store writes in its own transaction, validated here
 * (no claim-check: these events are a few bytes).
 */
/** Node's append to a run's stream, for `JournalService.prepare`. */
export function nodeAppend(
  run: Pick<HarnessRun, 'orgId' | 'sessionId' | 'runId' | 'parent'>,
  events: JournalEventInput[],
): JournalAppend {
  return {
    orgId: run.orgId,
    sessionId: run.sessionId,
    streamId: journalStreamId(run),
    runId: run.runId,
    writer: 'node' as const,
    events,
  };
}

export function nodeJournalAppend(
  run: Pick<HarnessRun, 'orgId' | 'sessionId' | 'runId' | 'parent'>,
  events: JournalEventInput[],
): PreparedJournalAppend {
  const append = nodeAppend(run, events);
  validateJournalAppend(append);
  return append;
}

/** The v2 admission's journal append, validated; the store writes it in its transaction. */
/**
 * What Node tells the person beside a run's steps (CTL-5b, `x.pipeshub.run.notice`): `mcp_unavailable` with
 * run.admitted, one per server left out (MCPNOTE-1); `signin_unavailable` on a parent whose code_task child runs on
 * the org's model access because no live worker carries the person's own sign-in (SIGNGAP).
 */
export type RunNotice =
  | 'signin_unavailable'
  | 'resumed'
  | McpUnavailableNotice
  | McpToolHeldNotice
  | CodeToolsNotice
  | EngineSwitchedNotice;

/**
 * MCPNOTE-1: the MCP server (`item`, its name) the run went without, and why. `instance` only when the person can
 * sign in to it again from the chat, or when it waits for an admin's approval (the link opens that server); the notice is the run owner's alone (`visibility: owner`, and a shared
 * viewer's saved answer drops it).
 */
export interface McpUnavailableNotice {
  code: 'mcp_unavailable';
  item: string;
  reason?: UnlistedMcpReason;
  instance?: string;
}

/** D-MCP-5: the tools of the server `item` the description scanner held back from the run; the owner's alone. */
export interface McpToolHeldNotice {
  code: 'mcp_tool_held';
  item: string;
  tools: string[];
}

/**
 * G-48: why an engine agent's turn ran without code tools, in the person's terms (the chat words each): the engine
 * cannot run on this server now, an admin paused it, the org's rules do not let this turn use it, no sandbox here
 * gives the isolation the org requires, or an admin turned the engine off and no other is on for it (WP-111).
 */
export const CODE_TOOLS_WITHHELD = [
  'not_available',
  'paused_by_admin',
  'not_allowed',
  'isolation_unavailable',
  'engine_off',
] as const;
export type CodeToolsWithheld = (typeof CODE_TOOLS_WITHHELD)[number];

/** G-48: the turn runs on the loop without `code_task`, and the person reads why. */
export interface CodeToolsNotice {
  code: 'ran_without_code_tools';
  item: CodeToolsWithheld;
}

/** WP-111: the chosen engine (`item`) is turned off, so the turn's code work runs on `engine`, which is on. */
export interface EngineSwitchedNotice {
  code: 'engine_switched';
  item: string;
  engine: string;
}

/** One notice as Node journals it: presentation only, the run's owner sees it. */
export const runNoticeEvent = (notice: RunNotice): JournalEventInput => ({
  type: 'x.pipeshub.run.notice',
  schemaVersion: 1,
  critical: false,
  visibility: 'owner',
  data: typeof notice === 'string' ? { code: notice } : { ...notice },
});

/**
 * CH-01: a claim that follows an attempt which never handed the run back (its sandbox or worker died, its lease
 * ran out) tells the person the task was interrupted and carried on from where it stopped. A drain or a wait
 * yields first and resets `attempt`, so a rolling deploy says nothing (CH-15).
 */
export const resumedNotices = (attempt: number): JournalEventInput[] =>
  attempt > 1 ? [runNoticeEvent('resumed')] : [];

export function admittedJournalAppend(
  run: Pick<
    HarnessRun,
    | 'orgId'
    | 'sessionId'
    | 'runId'
    | 'actorId'
    | 'sessionRole'
    | 'input'
    | 'attribution'
  >,
  via: RunVia,
  imported?: JournalEventInput | readonly JournalEventInput[],
  runKind: AdmittedRunKind = 'interactive',
  notices: readonly RunNotice[] = [],
): PreparedJournalAppend {
  const importing =
    imported === undefined
      ? []
      : Array.isArray(imported)
        ? imported
        : [imported];
  return nodeJournalAppend(run, [
    ...(importing as JournalEventInput[]),
    runAdmittedEvent(
      principalRefFor(run.actorId),
      via,
      run.sessionRole,
      runKind,
      run.attribution,
    ),
    ...notices.map(runNoticeEvent),
    ...(run.input?.command !== undefined
      ? [commandExpandedEvent(run.input.command, run.input.query)]
      : []),
  ]);
}

/**
 * Every claim. `attempt` counts claims since the run last left `running`
 * cleanly, so a run that suspends or yields is never mistaken for a crash loop.
 */
export const runClaimedEvent = (
  attempt: number,
  fence: number,
  leaseMs: number,
): JournalEventInput =>
  nodeRunEvent('run.claimed', { attempt, fence, leaseMs });

export const runSpecPinnedEvent = (pinned: PinnedRunSpec): JournalEventInput =>
  nodeRunEvent('run.spec_pinned', {
    specDigest: pinned.specDigest,
    spec: pinned.spec,
    capabilities: pinned.capabilities,
  });

export function transitionJournalEvents(
  run: HarnessRun,
  transition: RunTransition | undefined,
): JournalEventInput[] {
  if (run.harnessMajor !== 2 || transition === undefined) return [];
  const event = nodeRunEvent;
  const detail = transition.detail;
  if (transition.status === 'completed') {
    return [
      event('run.completed', {
        outcome:
          transition.outcome === 'needs_input' ? 'needs_input' : 'answered',
        ...(transition.truncatedBy !== undefined
          ? { truncatedBy: transition.truncatedBy }
          : {}),
      }),
    ];
  }
  if (transition.status === 'failed' && detail?.kind === 'quarantine') {
    return [
      event('run.quarantined', {
        attempts: detail.attempts,
        ...(detail.lastErrorType !== undefined
          ? { lastErrorType: detail.lastErrorType }
          : {}),
      }),
    ];
  }
  if (transition.status === 'failed') {
    const message = detail?.kind === 'fail' ? detail.message : undefined;
    const servers = detail?.kind === 'fail' ? detail.servers : undefined;
    const memoryLimitMb =
      detail?.kind === 'fail' ? detail.memoryLimitMb : undefined;
    return [
      event('run.failed', {
        errorType: transition.errorType ?? 'worker_failed',
        ...(message !== undefined ? { message } : {}),
        ...(servers !== undefined && servers.length > 0
          ? { servers: [...new Set(servers)] }
          : {}),
        ...(memoryLimitMb !== undefined ? { memoryLimitMb } : {}),
      }),
    ];
  }
  if (transition.status === 'cancelled') return [event('run.cancelled', {})];
  if (detail?.kind === 'suspend') {
    return [
      event('run.suspended', {
        reason: detail.reason,
        ...(detail.interactionId !== undefined
          ? { interactionId: detail.interactionId }
          : {}),
      }),
    ];
  }
  if (detail?.kind === 'yield') {
    return [
      event('run.yielded', {
        notBefore: detail.notBefore,
        reason: detail.reason,
      }),
    ];
  }
  return [];
}

/**
 * The tool calls of `runId` with a `tool.intent` or `tool.denied` and no
 * `tool.outcome` or `tool.unknown` after it, in the order they were opened
 * (EV-06, C2.49). A re-call after `tool.reconciled` not_applied opens its call
 * again. `events`: the run's journal, read back (not sealed).
 */
export function openToolCalls(
  events: readonly Pick<JournalEvent, 'runId' | 'type' | 'data'>[],
  runId: string,
): string[] {
  const ended = new Map<string, boolean>();
  const outcomes = new Set<string>();
  for (const event of events) {
    const invocation = event.data?.['invocationId'];
    if (event.runId !== runId || typeof invocation !== 'string') continue;
    if (event.type === 'tool.intent' || event.type === 'tool.denied') {
      ended.set(invocation, outcomes.has(invocation));
    } else if (event.type === 'tool.outcome' || event.type === 'tool.unknown') {
      ended.set(invocation, true);
      if (event.type === 'tool.outcome') outcomes.add(invocation);
    }
  }
  return [...ended]
    .filter(([, done]) => !done)
    .map(([invocation]) => invocation);
}

/**
 * What Node journals for a call a run left open when Node ends the run itself
 * (no worker holds it): a cancelled outcome, ahead of the terminal event, so
 * every call ends with exactly one outcome (EV-06). Node read nothing of the
 * call: no result, the platform's integrity.
 */
export const closingToolOutcome = (invocationId: string): JournalEventInput =>
  nodeRunEvent('tool.outcome', {
    invocationId,
    status: 'cancelled',
    labels: { integrity: 'internal' },
  });

/**
 * Node-authored events for a v2 control action (cancel or an interaction
 * resolved), journaled in the control transaction. `open`: the run's open
 * tool calls (`openToolCalls`), closed when the cancel ends the run here.
 */
export function controlJournalEvents(
  run: HarnessRun,
  command: Pick<ControlRun, 'action' | 'by' | 'resolution' | 'stop' | 'cause'>,
  open: readonly string[] = [],
): JournalEventInput[] {
  if (run.harnessMajor !== 2) return [];
  const by = command.by ?? principalRefFor(run.actorId);
  if (command.action === 'cancel') {
    const requested = nodeRunEvent('run.cancel_requested', {
      by,
      ...(command.stop !== undefined ? { stop: command.stop } : {}),
    });
    return WORKER_OWNED_RUN_STATUSES.has(run.status)
      ? [requested]
      : [
          requested,
          ...open.map(closingToolOutcome),
          nodeRunEvent('run.cancelled', { by }),
        ];
  }
  if (command.action === 'suspend') {
    return [
      nodeRunEvent('run.suspended', { reason: RETIRED }),
      nodeRunEvent('x.pipeshub.run.notice', { code: 'engine_retired' }),
    ];
  }
  if (command.action === 'resume' && command.cause === 'version_available') {
    return [
      nodeRunEvent('run.resumed', { cause: 'version_available' }),
      nodeRunEvent('x.pipeshub.run.notice', { code: 'engine_continued' }),
    ];
  }
  const resolution = command.resolution;
  if (resolution === undefined) return [];
  return [
    nodeRunEvent('interaction.resolved', resolvedData(resolution, by)),
    nodeRunEvent('run.resumed', { cause: 'resolved' }),
  ];
}

/**
 * Terminal events the sweeper writes for a v2 run (deadline, lapsed cancel),
 * after a cancelled outcome for each call in `open` (EV-06).
 */
/**
 * Why the sweeper failed a run: its deadline passed, or its worker was lost and
 * its pin turned automatic resumption off (C4.10 `recovery.autoResume`).
 */
export type SweptFailure = 'deadline_exceeded' | typeof WORKER_LOST;
export const WORKER_LOST = 'worker_lost';

/**
 * Whether a run whose worker's lease lapsed is queued again: off only where its
 * pin says so (C4.10); a lapsed cancel and a passed deadline end it either way.
 */
export const resumesAfterLostWorker = (
  run: Pick<HarnessRun, 'autoResume'>,
): boolean => run.autoResume !== false;

export function sweptJournalEvents(
  run: Pick<HarnessRun, 'harnessMajor'>,
  nextStatus: RunStatus,
  open: readonly string[] = [],
  errorType: SweptFailure = 'deadline_exceeded',
): JournalEventInput[] {
  if (run.harnessMajor !== 2) return [];
  const closing = open.map(closingToolOutcome);
  if (nextStatus === 'failed') {
    return [...closing, nodeRunEvent('run.failed', { errorType })];
  }
  if (nextStatus === 'cancelled') {
    return [...closing, nodeRunEvent('run.cancelled', {})];
  }
  return [];
}
