import { assertRetirable, assertRetired } from '../domain/operator-suspension';
import { RETIRED } from '../domain/operator-controls';
import { personWaitWindowMs } from '../domain/run-policy-fields';
import type { ApproverNoticeOutbox } from '../ports/approval-links';
import { approverNoticeId } from '../domain/approval-links';
import { recordedRunFieldsOf } from '../domain/recorded-run-fields';
import type { CommitFence } from '../ports/commit-fence';
import type { RunLists } from '../ports/run-lists';
import {
  compareNewestFirst,
  EndedRunFact,
  ListedJob,
  ListedRun,
  listedCarry,
  listedEngine,
  matchesPersonRunQuery,
  MAX_SESSION_OPEN_RUNS,
  PersonRunQuery,
} from '../domain/run-lists';
/* eslint-disable @typescript-eslint/require-await -- This deterministic adapter implements the asynchronous store port without I/O. */
import { admittedRunKind } from '../domain/run-kind';
import type { Heads, ResolvedRunConfig } from '../../control-plane';
import { memoryPin, MemoryPin } from '../domain/memory';
import type { SessionRepository } from '../domain/repository';
import { RUN_GATES_FEATURE, type RunGates } from '../domain/run-gates';
import {
  AnsweredRun,
  answeredRunFrom,
  AnsweredRunPin,
  type RunParent,
} from '../domain/answered-run';
import { RunKeyDigest } from '../domain/run-key-digest';
import {
  CompactRequest,
  CompactRequests,
  withCompact,
} from '../domain/compact-requests';
import { ephemeralRunKeyDigest } from './crypto/platform-run-key-digest';
import { RunTranscript } from '../ports/conversation-writer';
import { InMemoryBudgetLedger } from './in-memory-budget-ledger';
import {
  HarnessConflictError,
  ConversationDeletedError,
  HarnessNotFoundError,
  TranscriptProjectionError,
} from '../domain/errors';
import { canonicalJson, canonicalSha256 } from '../contracts/canonical-json';
import {
  admittedJournalAppend,
  childOf,
  ClaimCandidate,
  ClaimedRun,
  ClaimPinning,
  ClaimRole,
  ClaimRun,
  CommitBatch,
  CommitReceipt,
  ControlRun,
  controlResolutions,
  ancestorHeld,
  ancestorReleased,
  entersPersonWait,
  startsWaitingForPerson,
  heldWorkLeft,
  journalStreamId,
  nodeAppend,
  nodeJournalAppend,
  operatorStopAfterCancel,
  runClaimedEvent,
  resumedNotices,
  DispatchRecord,
  HarnessEvent,
  IDLE_RUN_STATUSES,
  HarnessRun,
  joinDeadline,
  personWaitDeadline,
  ReleaseAttempt,
  RenewAttempt,
  RunStatus,
  StoreClock,
  sweptJournalEvents,
  resumesAfterLostWorker,
  WORKER_LOST,
  type SweptFailure,
  systemStoreClock,
  TERMINAL_RUN_STATUSES,
  transitionEffects,
  validateCommitBatch,
  waitForPersonMs,
  WORKER_OWNED_RUN_STATUSES,
  endsWithoutTranscript,
  heldByAnotherAttempt,
} from '../domain/execution';
import { Followup, FollowupsWrite } from '../domain/followups';
import { PreparedJournalAppend, PreparedJournalEvent } from '../domain/journal';
import {
  assertControlCurrent,
  assertResolvable,
  assertSuspendable,
  DECIDABLE_RUN_STATUSES,
  openAfterCommit,
  RESUMABLE_RUN_STATUSES,
  resolutionFeatures,
  withoutInteraction,
} from '../domain/interactions';
import type { RunSurface } from '../domain/admission';
import { PinnedRunSpec, RunVia } from '../domain/run-spec';
import type { RunTrigger } from '../domain/schedules';
import type { PinnedEngineProfile } from '../domain/engine-profiles';
import type { PinnedModelAccess } from '../domain/model-access';
import {
  ClaimSubject,
  ActorRuns,
  OpenRun,
  OpenRunFilter,
  OperatorRuns,
  ExecutionStore,
  PendingApprovalFilter,
  UnansweredQuestionFilter,
  RunEnding,
  StoredReceipt,
  SweptTranscriptOutcome,
  SweptTranscripts,
  StrandedRun,
  WaitingRun,
} from '../ports/execution-store';
import { InMemoryJournalStore } from './in-memory-journal-store';
import { JournalPreparer, validatingPreparer } from '../ports/journal-store';
import {
  admissionRequirements,
  ClaimingWorker,
  missingFor,
  placesRun,
  raisedByClaim,
  requirementsOf,
  RunRequirements,
  unattemptedRelease,
  withFeature,
  WorkerFeature,
  workerIncompatible,
} from '../domain/worker-compat';
import {
  assertReferencesChecked,
  inputQueuedEvent,
  noRunToSteer,
  remainingInputs,
  steeringConflict,
  steeringFeatures,
  assertSteeredByActor,
  steeringRefusal,
} from '../domain/steering';
import {
  AdmissionIdentity,
  admissionScopeKey,
  AdmitRunCommand,
  RunAdmissionResult,
  RunAdmissionStore,
  assertPinCurrent,
  notLastAnswer,
  rootRunDocument,
  rootRunRequirements,
  durableSessionTitle,
} from '../domain/admission';
import { AgentRunDefinition } from '../domain/agent-definition';
import {
  SessionRole,
  sessionRoleOf,
  type TurnRole,
} from '../domain/session-roles';
import { SessionExecutionScope } from '../ports/chat-session-repository';
import { SessionRoleLookup } from '../ports/session-roles';
import {
  RunAudience,
  runAudience,
  seenRuns,
  SESSION_ROLES_FEATURE,
  SessionContext,
} from '../domain/session-audience';
import {
  LabelledTurn,
  MAX_VISIBLE_RUNS,
  TurnLabel,
} from '../domain/turn-labels';
import {
  NextRunEraser,
  NextRunOutcome,
  NextRunQueue,
  NextRunRecord,
  queuedNextRun,
  waitsAsNextRun,
  withoutInput,
} from '../domain/next-runs';
import {
  assertChildBound,
  ChildEnding,
  childCompletedEvent,
  childDoneResumedEvent,
  childFeatures,
  childRunDocument,
  endingOf,
  parentFeatures,
  sessionChildFeatures,
  joinWakeCommand,
  joinWakes,
  sweptEnding,
} from '../domain/child-runs';
import {
  AdmitChildCommand,
  ChildAdmission,
  ChildRunStore,
} from '../ports/child-runs';
import {
  ADMISSION_QUOTAS,
  cappedQuotas,
  AdmissionQuotas,
  advanceWindow,
  assertAdmissionQuotas,
  assertAdmissionRate,
  orgQuotaKey,
  QUOTA_ACTIVE_STATUSES,
  QuotaWindow,
  userQuotaKey,
} from '../domain/admission-quotas';
import {
  afterApproval,
  priorityClassOf,
  priorityFields,
  priorityRank,
  type ClaimOrder,
} from '../domain/priority-class';

const copy = <T>(value: T): T => structuredClone(value);

const sweptJournal = (
  run: HarnessRun,
  nextStatus: RunStatus,
  open: readonly string[] = [],
  errorType: SweptFailure = 'deadline_exceeded',
): PreparedJournalAppend[] => {
  const events = sweptJournalEvents(run, nextStatus, open, errorType);
  return events.length > 0 ? [nodeJournalAppend(run, events)] : [];
};
const scope = (...parts: string[]): string => JSON.stringify(parts);

/** A parent's side of a child ending, validated before anything is applied. */
interface PreparedJoin {
  parent: HarnessRun;
  open: number;
  appends: PreparedJournalAppend[];
  wakes: boolean;
}

export class InMemoryExecutionStore
  implements
    ActorRuns,
    OperatorRuns,
    RunLists,
    ExecutionStore,
    RunAdmissionStore,
    ChildRunStore,
    NextRunQueue,
    NextRunEraser,
    SessionRoleLookup,
    CompactRequests
{
  private readonly runs = new Map<string, HarnessRun>();
  private readonly admissionReceipts = new Map<
    string,
    { payloadHash: string; result: RunAdmissionResult }
  >();
  private readonly sessionOwners = new Map<
    string,
    { actorId: string; agentKey: string | null }
  >();
  /** Plays the conversation's shares (SS2): user id to access level, by session. */
  private readonly sessionShares = new Map<string, Map<string, string>>();
  /** Everyone a run was admitted for, by session (SS2). */
  private readonly sessionActors = new Map<string, string[]>();
  /** The person's `/compact` waiting for the session's next run (SLASH2 C2). */
  private readonly compactRequests = new Map<string, CompactRequest>();
  private readonly audiences = new Map<string, RunAudience>();
  private readonly memories = new Map<string, MemoryPin>();
  private readonly resolutions = new Map<string, ResolvedRunConfig>();
  /** The org's head and the deployment's revision the admission's head check reads (CPD-5, CPD-10); the control plane's in-memory heads in tests. */
  controlPlaneHeads: (orgId: string) => Heads = () => ({
    org: 0,
    deployment: 0,
  });
  private writes: Promise<unknown> = Promise.resolve();
  /** Ended turns' labels (P2.5a), by run, kept off HarnessRun as Mongo keeps them. */
  private readonly labels = new Map<string, TurnLabel>();
  /** Next runs (SS2) in queue order; settled ones keep their outcome. */
  private readonly nextRuns = new Map<
    string,
    NextRunRecord & { status: 'pending' | NextRunOutcome['status'] }
  >();
  private nextId = 0;
  private readonly activeSessions = new Map<string, string>();
  private readonly events = new Map<string, HarnessEvent[]>();
  private readonly commitReceipts = new Map<
    string,
    { payload: string; receipt: CommitReceipt }
  >();
  /** Dispatch commands in write order, as the shared outbox would hold them. */
  readonly dispatches: DispatchRecord[] = [];
  /** WP56-OUTBOX: written in the commit that makes a run wait, as the Mongo store's transaction does. */
  approverNotices: ApproverNoticeOutbox | null = null;

  private readonly workspaceHeads = new Map<string, number>();
  /** Admission-time grants by run, kept off HarnessRun as Mongo keeps them. */
  private readonly agentDefinitions = new Map<string, AgentRunDefinition>();
  private readonly engineProfiles = new Map<string, PinnedEngineProfile>();
  /** DEEPROUTE: runs admitted while the org's automatic hand-off was off. */
  private readonly autoRouteOff = new Set<string>();
  private readonly repositories = new Map<string, SessionRepository>();
  private readonly gates = new Map<string, RunGates>();
  private readonly answers = new Map<string, AnsweredRunPin>();
  private readonly modelAccesses = new Map<string, PinnedModelAccess>();
  private readonly triggers = new Map<string, RunTrigger>();
  private readonly vias = new Map<string, RunVia>();
  private readonly surfaces = new Map<string, RunSurface>();
  private readonly pins = new Map<string, PinnedRunSpec>();
  /** What each session's root stream needs a worker to fold, by session (SS1). */
  private readonly journalFeatures = new Map<string, WorkerFeature[]>();
  private readonly quotaWindows = new Map<string, QuotaWindow>();

  /** `clock` plays the database clock: lease and deadline predicates read it. */
  constructor(
    private readonly clock: StoreClock = systemStoreClock,
    readonly journal: InMemoryJournalStore = new InMemoryJournalStore(clock),
    private readonly quotas: AdmissionQuotas = ADMISSION_QUOTAS,
    /** Plays the conversation module; it writes before the commit mutates anything. */
    private readonly transcripts: TranscriptSink | null = null,
    private readonly journals: JournalPreparer = validatingPreparer,
    /** P6.5: opens a child's budget account inside its admission. */
    readonly budgets: InMemoryBudgetLedger | null = null,
    private readonly runKeys: RunKeyDigest = ephemeralRunKeyDigest(),
  ) {}

  /** Admissions in flight, by receipt key. */
  private readonly admitting = new Map<string, Promise<RunAdmissionResult>>();

  async findAdmission(
    command: AdmissionIdentity & Pick<AdmitRunCommand, 'payloadHash'>,
  ): Promise<RunAdmissionResult | null> {
    const previous = this.admissionReceipts.get(admissionScopeKey(command));
    if (previous === undefined) return null;
    if (previous.payloadHash !== command.payloadHash) {
      throw new HarnessConflictError(
        'IDEMPOTENCY_CONFLICT',
        'The idempotency key was already used with a different payload',
      );
    }
    return { ...copy(previous.result), created: false };
  }

  async requestCompact(
    orgId: string,
    sessionId: string,
    request: CompactRequest,
  ): Promise<boolean> {
    const sessionScope = scope(orgId, sessionId);
    if (!this.sessionActors.has(sessionScope)) return false;
    this.compactRequests.set(sessionScope, { ...request });
    return true;
  }

  async admit(
    command: AdmitRunCommand,
    callerNow: number,
  ): Promise<RunAdmissionResult> {
    const scopeKey = admissionScopeKey(command);
    // An identical admission in flight is waited for, then replayed, as the stores' quota holds have it (QA21-1).
    for (
      let twin = this.admitting.get(scopeKey);
      twin !== undefined;
      twin = this.admitting.get(scopeKey)
    ) {
      await twin.catch(() => undefined);
    }
    const admitted = this.admitOnce(command, callerNow);
    this.admitting.set(scopeKey, admitted);
    try {
      return await admitted;
    } finally {
      if (this.admitting.get(scopeKey) === admitted)
        this.admitting.delete(scopeKey);
    }
  }

  private async admitOnce(
    command: AdmitRunCommand,
    callerNow: number,
  ): Promise<RunAdmissionResult> {
    // The caller chose a duration; the deadline is anchored on store time.
    const now = this.clock.now();
    const absoluteDeadlineAt = now + (command.absoluteDeadlineAt - callerNow);
    if (absoluteDeadlineAt <= now) {
      throw new HarnessConflictError(
        'DEADLINE_EXCEEDED',
        'Run deadline must be in the future',
      );
    }
    const scopeKey = admissionScopeKey(command);
    const replay = await this.findAdmission(command);
    if (replay !== null) return replay;

    const agentKey =
      command.agentKey !== undefined && command.agentKey.length > 0
        ? command.agentKey
        : null;
    const sessionId = command.sessionId ?? `session-${String(++this.nextId)}`;
    const sessionScope = scope(command.orgId, sessionId);
    const role: TurnRole =
      command.asCollaborator === true ? 'collaborator' : 'owner';
    let context: SessionContext = {
      ownerId: command.actorId,
      shared: false,
      actors: [],
    };
    if (command.sessionId !== null) {
      const owner = this.sessionOwners.get(sessionScope);
      const shares = this.sessionShares.get(sessionScope);
      const bound =
        owner?.agentKey === agentKey &&
        (role === 'collaborator'
          ? shares?.get(command.actorId) === 'write'
          : owner.actorId === command.actorId);
      if (!bound) throw new HarnessNotFoundError();
      context = {
        ownerId: owner.actorId,
        shared: (shares?.size ?? 0) > 0,
        actors: this.sessionActors.get(sessionScope) ?? [],
      };
      // This store holds no conversation messages: no answer id is ever this conversation's (QA-26).
      if (command.regenerate !== undefined) throw new HarnessNotFoundError();
    }
    const activeId = this.activeSessions.get(sessionScope);
    const active =
      activeId !== undefined ? this.requireIndexedRun(activeId) : undefined;
    if (active !== undefined && !TERMINAL_RUN_STATUSES.has(active.status)) {
      if (
        command.nextRunId !== undefined ||
        command.regenerate !== undefined ||
        command.input.sandboxCommand !== undefined
      ) {
        throw new HarnessConflictError(
          'RUN_ACTIVE',
          'The session already has an active root run',
        );
      }
      if (waitsAsNextRun(active, command)) {
        return this.enqueueNextRun(
          { ...command, absoluteDeadlineAt },
          active,
          now,
          scopeKey,
        );
      }
      return this.steer(command, active, now, scopeKey, callerNow);
    }
    if (command.steerOnly === true) throw noRunToSteer();
    // A regenerate needs a conversation to regenerate in, as on MongoDB.
    if (command.regenerate !== undefined) throw notLastAnswer();
    // Checked and recorded with no await in between: atomic, as the Mongo
    // transaction is, and recorded only once every other check passed.
    assertPinCurrent(command, this.controlPlaneHeads(command.orgId));
    const quotaWindows = this.admissionQuotas(command, now);
    const runKey = scope(command.orgId, command.runId);
    if (this.runs.has(runKey)) {
      throw new HarnessConflictError(
        'RUN_ID_CONFLICT',
        'Run ID is already in use',
      );
    }

    const waiting =
      command.sessionId !== null
        ? this.compactRequests.get(sessionScope)
        : undefined;
    const input = withCompact(
      { ...copy(command.input), previousConversations: [] },
      command.harnessMajor,
      waiting,
    );
    if (input.compact !== undefined) this.compactRequests.delete(sessionScope);
    const run = rootRunDocument(command, {
      sessionId,
      userMessageKey: `message-${String(++this.nextId)}`,
      idempotencyKey: this.runKeys.digest(
        command.orgId,
        command.idempotencyKey,
      ),
      absoluteDeadlineAt,
      input,
      now,
    });
    const audience = runAudience(
      command.actorId,
      role,
      context,
      [...this.runs.values()]
        .filter(
          (earlier) =>
            earlier.orgId === command.orgId &&
            earlier.sessionId === sessionId &&
            earlier.actorId === command.actorId &&
            earlier.parent === undefined,
        )
        .map((earlier) => earlier.runId),
      command.visibleRuns ?? [],
    );
    if (audience !== null) run.sessionRole = audience.sessionRole;
    const memory = memoryPin(command.memory, audience !== null);
    if (memory !== null) {
      this.memories.set(scope(command.orgId, command.runId), memory);
    }
    const requires = rootRunRequirements(
      run,
      command,
      this.journalFeatures.get(sessionScope) ?? [],
      audience,
      memory,
    );
    if (requires !== undefined) run.requires = requires;
    if (command.sessionId === null) {
      this.sessionOwners.set(sessionScope, {
        actorId: command.actorId,
        agentKey,
      });
    }
    this.sessionActors.set(sessionScope, [
      ...new Set([...context.actors, command.actorId]),
    ]);
    if (audience !== null) {
      this.audiences.set(scope(command.orgId, command.runId), audience);
      this.journalFeatures.set(sessionScope, [
        ...new Set<WorkerFeature>([
          ...(this.journalFeatures.get(sessionScope) ?? []),
          SESSION_ROLES_FEATURE,
        ]),
      ]);
    }
    for (const [key, window] of quotaWindows)
      this.quotaWindows.set(key, window);
    this.runs.set(runKey, run);
    if (command.agentDefinition !== undefined) {
      this.agentDefinitions.set(runKey, copy(command.agentDefinition));
    }
    if (command.engineProfile !== undefined) {
      this.engineProfiles.set(runKey, copy(command.engineProfile));
    }
    if (command.codeTaskAutoRoute === false) this.autoRouteOff.add(runKey);
    if (command.repository !== undefined) {
      this.repositories.set(runKey, copy(command.repository));
    }
    if (command.gates !== undefined) {
      this.gates.set(runKey, copy(command.gates));
      this.journalFeatures.set(sessionScope, [
        ...new Set<WorkerFeature>([
          ...(this.journalFeatures.get(sessionScope) ?? []),
          RUN_GATES_FEATURE,
        ]),
      ]);
    }
    if (command.answered !== undefined) {
      this.answers.set(runKey, copy(command.answered));
    }
    if (command.resolved !== undefined) {
      this.resolutions.set(runKey, copy(command.resolved));
    }
    if (command.modelAccess !== undefined) {
      this.modelAccesses.set(runKey, copy(command.modelAccess));
    }
    if (command.trigger !== undefined) {
      this.triggers.set(runKey, copy(command.trigger));
    }
    this.vias.set(runKey, command.via);
    if (command.surface !== undefined) {
      this.surfaces.set(runKey, command.surface);
    }
    this.activeSessions.set(sessionScope, runKey);
    if (run.harnessMajor === 2) {
      this.journal.appendNow(
        admittedJournalAppend(
          run,
          command.via,
          undefined,
          admittedRunKind(run, command.trigger !== undefined),
          command.notices,
        ),
      );
    }
    const eventSeq = this.appendEvent(run, 0, 'run_queued', now);
    // An inline run is claimed by the process that admitted it; the sweeper
    // dispatches it only if that claim does not come (promoteInline).
    if (run.profile === 'dispatched') {
      this.insertOutbox(
        command.orgId,
        {
          commandId: command.dispatchCommandId,
          runId: command.runId,
          kind: 'run_ready',
        },
        now,
      );
    }
    const result: RunAdmissionResult = {
      sessionId,
      userMessageKey: run.userMessageKey,
      run: copy(run),
      firstEventSeq: eventSeq,
      lastEventSeq: eventSeq,
      created: true,
    };
    this.admissionReceipts.set(scopeKey, {
      payloadHash: command.payloadHash,
      result: copy(result),
    });
    return command.sessionId === null
      ? { ...result, createdSession: { title: durableSessionTitle(command) } }
      : result;
  }

  /** The message joins the session's active run (T2); nothing else is written. */
  private async steer(
    command: AdmitRunCommand,
    active: HarnessRun,
    now: number,
    scopeKey: string,
    callerNow: number,
  ): Promise<RunAdmissionResult> {
    assertSteeredByActor(active, command.actorId);
    const refused = steeringRefusal(active, command.input);
    if (refused !== null) throw steeringConflict(refused);
    assertReferencesChecked(command.input, command.referencesAuthorized);
    const [org, user] = this.rateWindows(command, now);
    assertAdmissionRate(
      { orgWindow: org[1], userWindow: user[1] },
      now,
      this.quotas,
    );
    const messageSeq = (active.inputSeq ?? 0) + 1;
    const append = await this.journals.prepare(
      nodeAppend(active, [
        inputQueuedEvent(messageSeq, command.input, command.actorId),
      ]),
    );
    // Prepared (a claim-check put) before anything is recorded; the checks re-run after the await.
    if (
      (active.inputSeq ?? 0) + 1 !== messageSeq ||
      steeringRefusal(active, command.input) !== null
    ) {
      return this.admit(command, callerNow);
    }
    this.journal.appendNow(append);
    for (const [key, window] of [org, user]) this.quotaWindows.set(key, window);
    active.inputSeq = messageSeq;
    active.pendingInputs = [...(active.pendingInputs ?? []), messageSeq];
    active.requires = withFeature(
      requirementsOf(active),
      ...steeringFeatures(command.input),
    );
    const streamScope = scope(active.orgId, active.sessionId);
    this.journalFeatures.set(streamScope, [
      ...new Set([
        ...(this.journalFeatures.get(streamScope) ?? []),
        ...steeringFeatures(command.input),
      ]),
    ]);
    active.updatedAt = now;
    const result: RunAdmissionResult = {
      sessionId: active.sessionId,
      userMessageKey: active.userMessageKey,
      run: copy(active),
      firstEventSeq: 0,
      lastEventSeq: 0,
      created: true,
      queued: { messageSeq },
    };
    this.admissionReceipts.set(scopeKey, {
      payloadHash: command.payloadHash,
      result: copy(result),
    });
    return result;
  }

  /** A message to someone else's running task waits for it to end (SS2); only the rate windows count it. */
  private enqueueNextRun(
    command: AdmitRunCommand,
    active: HarnessRun,
    now: number,
    scopeKey: string,
  ): RunAdmissionResult {
    const [org, user] = this.rateWindows(command, now);
    assertAdmissionRate(
      { orgWindow: org[1], userWindow: user[1] },
      now,
      this.quotas,
    );
    for (const [key, window] of [org, user]) this.quotaWindows.set(key, window);
    const ahead = [...this.nextRuns.values()].filter(
      (next) =>
        next.status === 'pending' &&
        next.orgId === active.orgId &&
        next.sessionId === active.sessionId,
    ).length;
    const id = `next-${String(++this.nextId)}`;
    this.nextRuns.set(id, {
      id,
      orgId: active.orgId,
      sessionId: active.sessionId,
      actorId: command.actorId,
      runId: command.runId,
      command: copy(command),
      behindRunId: active.runId,
      createdAt: now,
      status: 'pending',
    });
    const result: RunAdmissionResult = {
      sessionId: active.sessionId,
      userMessageKey: '',
      run: withoutInput(active),
      firstEventSeq: 0,
      lastEventSeq: 0,
      created: true,
      nextRun: queuedNextRun({ id, runId: command.runId }, ahead + 1),
    };
    this.admissionReceipts.set(scopeKey, {
      payloadHash: command.payloadHash,
      result: copy(result),
    });
    return copy(result);
  }

  async nextPending(
    orgId: string,
    sessionId: string,
  ): Promise<NextRunRecord | null> {
    const next = [...this.nextRuns.values()].find(
      (entry) =>
        entry.status === 'pending' &&
        entry.orgId === orgId &&
        entry.sessionId === sessionId,
    );
    if (next === undefined) return null;
    const { status: _status, ...record } = next;
    return copy(record);
  }

  async drainable(
    limit: number,
  ): Promise<Array<{ orgId: string; sessionId: string }>> {
    const sessions = new Map<string, { orgId: string; sessionId: string }>();
    for (const next of this.nextRuns.values()) {
      if (next.status !== 'pending') continue;
      const key = scope(next.orgId, next.sessionId);
      const activeId = this.activeSessions.get(key);
      const active =
        activeId !== undefined ? this.runs.get(activeId) : undefined;
      if (active !== undefined && !TERMINAL_RUN_STATUSES.has(active.status)) {
        continue;
      }
      sessions.set(key, { orgId: next.orgId, sessionId: next.sessionId });
    }
    return [...sessions.values()].slice(0, Math.max(limit, 1));
  }

  async settleNextRun(
    orgId: string,
    id: string,
    outcome: NextRunOutcome,
  ): Promise<void> {
    const next = this.nextRuns.get(id);
    if (next?.orgId !== orgId || next.status !== 'pending') return;
    next.status = outcome.status;
  }

  async dropNextRuns(orgId: string, sessionId: string): Promise<number> {
    let dropped = 0;
    for (const [id, next] of this.nextRuns) {
      if (next.orgId === orgId && next.sessionId === sessionId) {
        this.nextRuns.delete(id);
        dropped += 1;
      }
    }
    this.compactRequests.delete(scope(orgId, sessionId));
    return dropped;
  }

  /** Plays the conversation module's share (SS2); `read` makes a viewer, `write` a collaborator. */
  shareSession(
    orgId: string,
    sessionId: string,
    userId: string,
    accessLevel: 'read' | 'write',
  ): void {
    const key = scope(orgId, sessionId);
    const shares = this.sessionShares.get(key) ?? new Map<string, string>();
    shares.set(userId, accessLevel);
    this.sessionShares.set(key, shares);
  }

  unshareSession(orgId: string, sessionId: string, userId: string): void {
    this.sessionShares.get(scope(orgId, sessionId))?.delete(userId);
  }

  /** The role lookup the conversation module answers, over the shares above. */
  async roleOf(
    sessionScope: SessionExecutionScope,
  ): Promise<SessionRole | null> {
    const key = scope(sessionScope.orgId, sessionScope.sessionId);
    const owner = this.sessionOwners.get(key);
    if (owner === undefined) return null;
    const shares = this.sessionShares.get(key) ?? new Map<string, string>();
    return sessionRoleOf(
      {
        ownerId: owner.actorId,
        isShared: shares.size > 0,
        shares: [...shares].map(([userId, accessLevel]) => ({
          userId,
          accessLevel,
        })),
      },
      sessionScope.actorId,
    );
  }

  /** The org's and the actor's rate windows once this admission counts. */
  private rateWindows(
    command: AdmitRunCommand,
    now: number,
  ): [[string, QuotaWindow], [string, QuotaWindow]] {
    const advance = (key: string): [string, QuotaWindow] => [
      key,
      advanceWindow(
        this.quotaWindows.get(key) ?? null,
        now,
        this.quotas.windowMs,
      ),
    ];
    return [
      advance(orgQuotaKey(command.orgId)),
      advance(userQuotaKey(command.orgId, command.actorId)),
    ];
  }

  private admissionQuotas(
    command: AdmitRunCommand,
    now: number,
  ): Array<[string, QuotaWindow]> {
    const [org, user] = this.rateWindows(command, now);
    const active = [...this.runs.values()].filter(
      (run) =>
        run.orgId === command.orgId &&
        QUOTA_ACTIVE_STATUSES.includes(run.status),
    );
    const state = {
      orgWindow: org[1],
      userWindow: user[1],
      orgActive: active.length,
      userActive: active.filter((run) => run.actorId === command.actorId)
        .length,
    };
    assertAdmissionQuotas(
      state,
      now,
      cappedQuotas(this.quotas, command.quotaCaps),
    );
    return [org, user];
  }

  async readClaimSubject(
    orgId: string,
    runId: string,
  ): Promise<ClaimSubject | null> {
    const runKey = scope(orgId, runId);
    const run = this.runs.get(runKey);
    if (run === undefined) return null;
    return {
      run: copy(run),
      agentDefinition: copy(this.agentDefinitions.get(runKey) ?? null),
      engineProfile: copy(this.engineProfiles.get(runKey) ?? null),
      codeTaskAutoRoute: this.autoRouteOff.has(runKey) ? false : null,
      repository: copy(this.repositories.get(runKey) ?? null),
      gates: copy(this.gates.get(runKey) ?? null),
      answered: copy(this.answers.get(runKey) ?? null),
      modelAccess: copy(this.modelAccesses.get(runKey) ?? null),
      via: this.vias.get(runKey) ?? 'session',
      surface: this.surfaces.get(runKey) ?? null,
      audience: copy(this.audiences.get(runKey) ?? null),
      trigger: copy(this.triggers.get(runKey) ?? null),
      memory: copy(this.memories.get(runKey) ?? null),
      pinned: copy(this.pins.get(runKey) ?? null),
      resolved: copy(this.resolutions.get(runKey) ?? null),
    };
  }

  async claim(command: ClaimRun, pinning?: ClaimPinning): Promise<ClaimedRun> {
    const now = this.clock.now();
    const runKey = scope(command.orgId, command.runId);
    const run = this.requireRun(command.orgId, command.runId);
    const expiredOwner =
      run.status === 'running' &&
      run.leaseExpiresAt !== null &&
      run.leaseExpiresAt <= now;
    const due = (run.notBefore ?? null) === null || (run.notBefore ?? 0) <= now;
    if ((run.status !== 'queued' || !due) && !expiredOwner) {
      throw new HarnessConflictError(
        'RUN_NOT_CLAIMABLE',
        'Run is not claimable',
      );
    }
    if (run.absoluteDeadlineAt <= now) {
      throw new HarnessConflictError(
        'DEADLINE_EXCEEDED',
        'Run deadline has elapsed',
      );
    }
    const worker = command.worker;
    if (worker !== undefined && missingFor(run, worker).length > 0) {
      throw workerIncompatible();
    }
    if (run.harnessMajor === 2 && !this.pins.has(runKey) && !pinning) {
      throw new HarnessConflictError(
        'SPEC_NOT_PINNED',
        'A harness v2 run needs a spec to pin on its first claim',
      );
    }
    run.claimedFrom = expiredOwner ? 'expired_lease' : 'queued';
    run.status = 'running';
    run.fence += 1;
    run.attempts = (run.attempts ?? 0) + 1;
    run.revision += 1;
    run.workerId = command.workerId;
    run.attemptId = command.attemptId;
    run.leaseExpiresAt = this.boundedLease(
      now,
      command.leaseDurationMs,
      run.absoluteDeadlineAt,
    );
    run.updatedAt = now;
    if (worker !== undefined) {
      if (run.harnessMajor === 2) {
        run.requires = raisedByClaim(requirementsOf(run), worker);
      }
      run.attemptFeatures = [...worker.features];
      run.waitingForWorker = null;
    }
    let pinned: PinnedRunSpec | undefined;
    if (run.harnessMajor === 2) {
      const events: PreparedJournalEvent[] = [
        runClaimedEvent(run.attempts, run.fence, command.leaseDurationMs),
        ...resumedNotices(run.attempts),
      ];
      pinned = this.pins.get(runKey);
      if (pinned === undefined && pinning !== undefined) {
        pinned = copy(pinning.pinned);
        this.pins.set(runKey, pinned);
        run.requires = {
          ...requirementsOf(run),
          schemaVersion: pinning.pinned.spec.schemaVersion,
        };
        events.push(copy(pinning.specEvent));
      }
      this.journal.appendNow({
        orgId: run.orgId,
        sessionId: run.sessionId,
        streamId: journalStreamId(run),
        runId: run.runId,
        attemptId: command.attemptId,
        fence: run.fence,
        writer: 'node',
        events,
      });
    }
    const stream = journalStreamId(run);
    return {
      run: copy(run),
      journal: { lastSeq: await this.journal.lastSeq(run.orgId, [stream]) },
      ...(pinned !== undefined ? { pinned: copy(pinned) } : {}),
    };
  }

  async nextClaimable(
    role: ClaimRole,
    tClaimMs: number,
    limit: number,
    worker?: ClaimingWorker,
    order: ClaimOrder = {},
  ): Promise<ClaimCandidate[]> {
    const due = this.due(role, tClaimMs).filter(
      (run) =>
        (worker === undefined || missingFor(run, worker).length === 0) &&
        (order.above === undefined ||
          (run.priorityRank ?? 0) < priorityRank(order.above)),
    );
    // A stable sort: within a class the due order stands, as the Mongo index's notBefore does.
    const ordered = order.floor
      ? due
      : due.sort(
          (left, right) =>
            (left.priorityRank ?? -1) - (right.priorityRank ?? -1),
        );
    return ordered
      .slice(0, Math.max(limit, 1))
      .map((run) => ({ orgId: run.orgId, runId: run.runId }));
  }

  async markWaiting(
    role: ClaimRole,
    tClaimMs: number,
    limit: number,
    worker: ClaimingWorker,
  ): Promise<WaitingRun[]> {
    const marked: WaitingRun[] = [];
    const hidden = this.due(role, tClaimMs)
      .filter(
        (run) =>
          (run.waitingForWorker ?? null) === null &&
          // A run of another region is another worker's, never one this worker leaves waiting (OWD-12).
          placesRun(run.region, worker) &&
          missingFor(run, worker).length > 0,
      )
      .slice(0, Math.max(limit, 1));
    for (const run of hidden) {
      const requires = requirementsOf(run);
      if (await this.markRunWaiting(run.orgId, run.runId, requires)) {
        marked.push({
          orgId: run.orgId,
          runId: run.runId,
          sessionId: run.sessionId,
          requires,
        });
      }
    }
    return marked;
  }

  async activeRunsOfActor(
    orgId: string,
    actorId: string,
  ): Promise<HarnessRun[]> {
    return [...this.runs.values()]
      .filter(
        (run) =>
          run.orgId === orgId &&
          run.actorId === actorId &&
          !TERMINAL_RUN_STATUSES.has(run.status),
      )
      .map(copy);
  }

  async personRuns(query: PersonRunQuery): Promise<ListedRun[]> {
    return [...this.runs.values()]
      .filter((run) => matchesPersonRunQuery(run, query))
      .sort(compareNewestFirst)
      .slice(0, query.limit)
      .map((run) => {
        const key = scope(run.orgId, run.runId);
        return {
          run: copy(run),
          engine: listedEngine(
            this.engineProfiles.get(key) ?? null,
            this.pins.get(key)?.spec ?? null,
          ),
        };
      });
  }

  async sessionJobs(
    orgId: string,
    sessionId: string,
    limit: number,
  ): Promise<ListedJob[]> {
    return [...this.runs.values()]
      .filter(
        (run) =>
          run.orgId === orgId &&
          run.sessionId === sessionId &&
          run.parent !== undefined,
      )
      .sort(compareNewestFirst)
      .slice(0, Math.max(0, limit))
      .map((run) => {
        const key = scope(run.orgId, run.runId);
        const spec = this.pins.get(key)?.spec ?? null;
        return {
          run: copy(run),
          engine: listedEngine(this.engineProfiles.get(key) ?? null, spec),
          continuesRunId: listedCarry(spec),
        };
      });
  }

  async openRunsOfSessions(
    orgId: string,
    sessionIds: readonly string[],
  ): Promise<HarnessRun[]> {
    const sessions = new Set(sessionIds);
    return [...this.runs.values()]
      .filter(
        (run) =>
          run.orgId === orgId &&
          sessions.has(run.sessionId) &&
          !TERMINAL_RUN_STATUSES.has(run.status),
      )
      .sort(compareNewestFirst)
      .slice(0, MAX_SESSION_OPEN_RUNS)
      .map(copy);
  }

  async lastEndedRuns(
    orgId: string,
    actorId: string,
    sessionIds: readonly string[],
  ): Promise<EndedRunFact[]> {
    const last = new Map<string, HarnessRun>();
    for (const run of this.runs.values()) {
      if (
        run.orgId !== orgId ||
        run.actorId !== actorId ||
        !sessionIds.includes(run.sessionId) ||
        !TERMINAL_RUN_STATUSES.has(run.status)
      ) {
        continue;
      }
      const held = last.get(run.sessionId);
      if (held === undefined || run.updatedAt > held.updatedAt) {
        last.set(run.sessionId, run);
      }
    }
    return sessionIds.flatMap((sessionId) => {
      const run = last.get(sessionId);
      return run === undefined
        ? []
        : [
            {
              sessionId,
              runId: run.runId,
              status: run.status,
              endedAt: run.updatedAt,
            },
          ];
    });
  }

  async openRuns(filter: OpenRunFilter, limit: number): Promise<OpenRun[]> {
    const open: OpenRun[] = [];
    for (const [key, run] of this.runs) {
      if (TERMINAL_RUN_STATUSES.has(run.status)) continue;
      const profile = this.engineProfiles.get(key);
      const engine =
        profile === undefined
          ? null
          : { kind: profile.engine.kind, version: profile.engine.version };
      if (
        (filter.orgId !== undefined && run.orgId !== filter.orgId) ||
        (filter.sessionId !== undefined &&
          run.sessionId !== filter.sessionId) ||
        (filter.agentKey !== undefined && run.agentKey !== filter.agentKey) ||
        (filter.suspendedFor !== undefined &&
          (run.status !== 'paused' ||
            run.suspendedFor !== filter.suspendedFor)) ||
        (filter.engine !== undefined &&
          (engine?.kind !== filter.engine.kind ||
            engine.version !== filter.engine.version)) ||
        (filter.engineOnly === true && engine === null)
      ) {
        continue;
      }
      open.push({ run: copy(run), engine });
    }
    return open
      .sort((a, b) => a.run.createdAt - b.run.createdAt)
      .slice(0, Math.max(0, limit));
  }

  async strandedRuns(
    feature: WorkerFeature,
    waitedMs: number,
    limit: number,
  ): Promise<StrandedRun[]> {
    const cutoff = this.clock.now() - waitedMs;
    return [...this.runs.entries()]
      .filter(
        ([, run]) =>
          run.status === 'queued' &&
          (run.requires?.features ?? []).includes(feature) &&
          run.waitingForWorker != null &&
          run.waitingForWorker.since <= cutoff,
      )
      .sort(
        ([, left], [, right]) =>
          (left.waitingForWorker?.since ?? 0) -
          (right.waitingForWorker?.since ?? 0),
      )
      .slice(0, Math.max(limit, 0))
      .map(([key, run]) => ({ run: copy(run), pinned: this.pins.has(key) }));
  }

  async relaxRequirement(
    orgId: string,
    runId: string,
    revision: number,
    feature: WorkerFeature,
  ): Promise<boolean> {
    const key = scope(orgId, runId);
    const run = this.runs.get(key);
    if (
      run?.status !== 'queued' ||
      run.revision !== revision ||
      this.pins.has(key) ||
      !(run.requires?.features ?? []).includes(feature)
    ) {
      return false;
    }
    run.requires = {
      ...(run.requires as RunRequirements),
      features: (run.requires as RunRequirements).features.filter(
        (f) => f !== feature,
      ),
    };
    run.waitingForWorker = null;
    run.revision += 1;
    run.updatedAt = this.clock.now();
    return true;
  }

  async markRunWaiting(
    orgId: string,
    runId: string,
    requires: RunRequirements,
  ): Promise<boolean> {
    const run = this.runs.get(scope(orgId, runId));
    if (run?.status !== 'queued' || (run.waitingForWorker ?? null) !== null) {
      return false;
    }
    run.waitingForWorker = {
      since: this.clock.now(),
      requires: copy(requires),
    };
    return true;
  }

  /** Queued v2 runs due for this role, oldest first (the Mongo queue's order). */
  private due(role: ClaimRole, tClaimMs: number): HarnessRun[] {
    const now = this.clock.now();
    return [...this.runs.values()]
      .filter(
        (run) =>
          run.status === 'queued' &&
          run.harnessMajor === 2 &&
          run.absoluteDeadlineAt > now &&
          (role === 'inline'
            ? run.profile === 'inline' && (run.notBefore ?? 0) <= now
            : (run.notBefore ?? 0) +
                (run.profile === 'inline' ? tClaimMs : 0) <=
              now),
      )
      .sort((left, right) => {
        const due =
          (left.notBefore ?? left.createdAt) -
          (right.notBefore ?? right.createdAt);
        return due !== 0 ? due : left.createdAt - right.createdAt;
      });
  }

  now(_orgId: string): Promise<number> {
    return Promise.resolve(this.clock.now());
  }

  async conversationDeleted(run: HarnessRun): Promise<boolean> {
    return this.transcripts?.sessionExists !== undefined
      ? !(await this.transcripts.sessionExists(copy(run)))
      : false;
  }

  async release(command: ReleaseAttempt): Promise<HarnessRun> {
    const now = this.clock.now();
    const run = this.requireOwnedAttempt(command, now);
    if (run.status !== 'running') {
      throw new HarnessConflictError(
        'INVALID_TRANSITION',
        'Only a running attempt can be released',
      );
    }
    run.status = 'queued';
    run.workerId = null;
    run.attemptId = null;
    run.leaseExpiresAt = null;
    run.notBefore = now;
    run.revision += 1;
    run.updatedAt = now;
    if (unattemptedRelease(command.reason)) {
      run.attempts = Math.max(0, (run.attempts ?? 0) - 1);
    }
    this.insertOutbox(
      run.orgId,
      {
        commandId: `release:${run.runId}:${String(run.fence)}`,
        runId: run.runId,
        kind: 'run_resume',
      },
      now,
    );
    return copy(run);
  }

  async unclaimedRuns(ageMs: number, limit: number): Promise<HarnessRun[]> {
    if (limit <= 0) return [];
    const cutoff = this.clock.now() - ageMs;
    return [...this.runs.entries()]
      .filter(
        ([key, run]) =>
          run.status === 'queued' &&
          run.attempts === 0 &&
          run.parent == null &&
          run.waitingForWorker == null &&
          !this.triggers.has(key) &&
          Math.max(run.updatedAt, run.notBefore ?? 0) <= cutoff,
      )
      .sort(([, left], [, right]) => left.updatedAt - right.updatedAt)
      .slice(0, limit)
      .map(([, run]) => copy(run));
  }

  async promoteInline(tClaimMs: number, limit: number): Promise<number> {
    const now = this.clock.now();
    const due = [...this.runs.values()]
      .filter(
        (run) =>
          run.status === 'queued' &&
          run.profile === 'inline' &&
          (run.notBefore ?? run.createdAt) + tClaimMs <= now,
      )
      .sort(
        (left, right) =>
          (left.notBefore ?? left.createdAt) -
          (right.notBefore ?? right.createdAt),
      )
      .slice(0, Math.max(limit, 1));
    for (const run of due) {
      run.profile = 'dispatched';
      run.revision += 1;
      run.updatedAt = now;
      this.insertOutbox(
        run.orgId,
        {
          commandId: `promote:${run.runId}:${String(run.revision)}`,
          runId: run.runId,
          kind: 'run_ready',
        },
        now,
      );
    }
    return due.length;
  }

  async renew(command: RenewAttempt): Promise<HarnessRun> {
    const now = this.clock.now();
    const run = this.requireOwnedAttempt(command, now);
    run.leaseExpiresAt = this.boundedLease(
      now,
      command.leaseDurationMs,
      run.absoluteDeadlineAt,
    );
    run.updatedAt = now;
    return copy(run);
  }

  async findReceipt(
    orgId: string,
    runId: string,
    commandId: string,
  ): Promise<StoredReceipt | null> {
    const previous = this.commitReceipts.get(scope(orgId, runId, commandId));
    return previous
      ? {
          payloadHash: canonicalSha256(JSON.parse(previous.payload) as unknown),
          receipt: copy(previous.receipt),
        }
      : null;
  }

  async commit(
    command: CommitBatch,
    journal: readonly PreparedJournalAppend[],
    transcript?: RunTranscript,
  ): Promise<CommitReceipt> {
    return this.oneWriter(() => this.commitNow(command, journal, transcript));
  }

  private async commitNow(
    command: CommitBatch,
    journal: readonly PreparedJournalAppend[],
    transcript?: RunTranscript,
  ): Promise<CommitReceipt> {
    const now = this.clock.now();
    const receiptKey = scope(command.orgId, command.runId, command.commandId);
    const canonical = canonicalJson(command);
    const previous = this.commitReceipts.get(receiptKey);
    if (previous) {
      if (previous.payload !== canonical) {
        throw new HarnessConflictError(
          'IDEMPOTENCY_CONFLICT',
          'The command ID was already used with a different transition',
        );
      }
      return { ...copy(previous.receipt), replayed: true };
    }

    const run = this.requireOwnedAttempt(command, now);
    const transition = command.transition;
    if (
      transition?.expectedRevision !== undefined &&
      run.revision !== transition.expectedRevision
    ) {
      throw new HarnessConflictError(
        'REVISION_CONFLICT',
        'Run revision changed',
      );
    }
    validateCommitBatch(run, command);
    assertSuspendable(run, command);
    const head = command.workspaceHeadCAS;
    const headKey = head ? scope(command.orgId, head.workspaceId) : '';
    if (
      head &&
      (this.workspaceHeads.get(headKey) ?? 0) !== head.expectedRevision
    ) {
      throw new HarnessConflictError(
        'WORKSPACE_CONFLICT',
        'Workspace HEAD moved since the expected revision',
      );
    }

    await this.saveTranscript(run, transcript, transition?.status);
    const join =
      transition !== undefined && TERMINAL_RUN_STATUSES.has(transition.status)
        ? this.prepareJoin(run, endingOf(transition))
        : null;
    // Everything above can refuse; nothing below can, and nothing below
    // yields before the run is updated, so concurrent callers see the commit
    // whole or not at all (as the Mongo transaction guarantees).
    if (head) this.workspaceHeads.set(headKey, head.revision);
    const appended = journal.map((append) => this.journal.appendNow(append));
    if (run.pendingInputs !== undefined) {
      run.pendingInputs = remainingInputs(run, command);
    }
    const open = openAfterCommit(run, command);
    if (open !== undefined) run.openInteractions = open;
    const recorded = recordedRunFieldsOf(command);
    if (recorded !== undefined) Object.assign(run, recorded);

    const eventScope = scope(run.orgId, run.sessionId);
    const storedEvents = this.events.get(eventScope) ?? [];
    const firstEventSeq =
      command.legacyEvents.length > 0 ? storedEvents.length + 1 : null;
    const commitSeq = run.revision + 1;
    const legacy = command.legacyEvents.map((event, index): HarnessEvent => ({
      orgId: run.orgId,
      sessionId: run.sessionId,
      runId: run.runId,
      eventSeq: storedEvents.length + index + 1,
      commitSeq,
      type: event.type,
      data: copy(event.data),
      createdAt: now,
    }));

    let holds = false;
    const waits =
      transition !== undefined &&
      startsWaitingForPerson(run, transition.status);
    if (transition) {
      const effects = transitionEffects(run, transition);
      if (entersPersonWait(run, transition.status, this.treeScheduled(run))) {
        Object.assign(
          run,
          personWaitDeadline(
            run,
            now,
            personWaitWindowMs(run, waitForPersonMs()),
          ),
        );
        holds = true;
      } else if (
        transition.status === 'paused' &&
        effects.suspendedFor === 'join'
      ) {
        Object.assign(run, joinDeadline(run, this.childDeadlines(run), now));
        holds = typeof run.workLeftMs === 'number';
      }
      run.status = transition.status;
      run.outcome = transition.outcome ?? null;
      if (transition.truncatedBy !== undefined) {
        run.truncatedBy = transition.truncatedBy;
      }
      run.checkpoint =
        transition.checkpoint !== undefined
          ? copy(transition.checkpoint)
          : run.checkpoint;
      if (effects.attempts !== undefined) run.attempts = effects.attempts;
      if (effects.notBefore !== undefined) {
        run.notBefore = Math.max(now, effects.notBefore);
      }
      if (effects.checkpointRef !== undefined) {
        run.checkpointRef = effects.checkpointRef;
      }
      if (effects.profile !== undefined) run.profile = effects.profile;
      if (effects.suspendedFor !== undefined) {
        run.suspendedFor = effects.suspendedFor;
      }
    }
    run.revision = commitSeq;
    run.updatedAt = now;
    if (!WORKER_OWNED_RUN_STATUSES.has(run.status)) {
      run.workerId = null;
      run.attemptId = null;
      run.leaseExpiresAt = null;
    }
    if (TERMINAL_RUN_STATUSES.has(run.status)) {
      this.activeSessions.delete(scope(run.orgId, run.sessionId));
    }
    this.events.set(eventScope, [...storedEvents, ...legacy]);
    for (const item of command.outbox)
      this.insertOutbox(command.orgId, item, now);
    if (waits && this.approverNotices !== null) {
      void this.approverNotices.enqueue(
        {
          id: approverNoticeId(run.orgId, run.runId, commitSeq),
          orgId: run.orgId,
          runId: run.runId,
          createdAt: now,
        },
        null,
      );
    }
    if (join !== null) this.applyJoin(run, join, now);
    if (holds) this.holdAncestors(run, now);

    const first = appended.at(0);
    const last = appended.at(-1);
    const receipt: CommitReceipt = {
      commandId: command.commandId,
      run: copy(run),
      firstEventSeq,
      lastEventSeq: legacy.at(-1)?.eventSeq ?? null,
      journal:
        first && last
          ? {
              streamId: first.streamId,
              firstSeq: first.firstSeq,
              lastSeq: last.lastSeq,
            }
          : null,
    };
    this.commitReceipts.set(receiptKey, {
      payload: canonical,
      receipt: copy(receipt),
    });
    return receipt;
  }

  async failRun(
    orgId: string,
    runId: string,
    errorType: string,
    journal: readonly PreparedJournalAppend[],
    holder?: string,
  ): Promise<boolean> {
    const run = this.requireRun(orgId, runId);
    if (TERMINAL_RUN_STATUSES.has(run.status)) return false;
    const now = this.clock.now();
    if (heldByAnotherAttempt(run, now, holder ?? null)) return false;
    this.expire(run, 'failed', 'run_failed', { errorType }, now, journal);
    return true;
  }

  private async saveTranscript(
    run: HarnessRun,
    transcript: RunTranscript | undefined,
    ending: RunStatus | undefined,
  ): Promise<void> {
    if (transcript === undefined) return;
    if (this.transcripts !== null) {
      let saved: boolean;
      try {
        saved = await this.transcripts.record(copy(run), transcript);
      } catch (error) {
        throw new TranscriptProjectionError(error);
      }
      if (!saved && !endsWithoutTranscript(ending))
        throw new ConversationDeletedError();
    }
    this.keepLabel(run, transcript);
  }

  private keepLabel(run: HarnessRun, transcript: RunTranscript): void {
    if (transcript.label !== undefined) {
      this.labels.set(scope(run.orgId, run.runId), copy(transcript.label));
    }
  }

  async sessionRunParents(
    orgId: string,
    sessionId: string,
  ): Promise<RunParent[]> {
    return [...this.runs.values()]
      .filter((run) => run.orgId === orgId && run.sessionId === sessionId)
      .map((run) => ({
        runId: run.runId,
        parentRunId: run.parent?.runId ?? null,
      }));
  }

  async answeredRun(
    orgId: string,
    sessionId: string,
    runId?: string,
  ): Promise<AnsweredRun | null> {
    const roots = this.rootRuns(orgId, sessionId);
    const run =
      runId !== undefined
        ? roots.find((root) => root.runId === runId)
        : roots[roots.length - 1];
    if (run === undefined) return null;
    const runKey = scope(orgId, run.runId);
    return answeredRunFrom(
      {
        runId: run.runId,
        status: run.status,
        outcome: run.outcome,
        gates: this.gates.get(runKey) ?? null,
        trigger: this.triggers.get(runKey) ?? null,
        answered: this.answers.get(runKey) ?? null,
      },
      runId !== undefined,
    );
  }

  async labelledTurns(
    orgId: string,
    sessionId: string,
    actorId: string,
  ): Promise<LabelledTurn[]> {
    return this.rootRuns(orgId, sessionId)
      .filter(
        (run) =>
          run.actorId !== actorId &&
          this.labels.get(scope(orgId, run.runId))?.complete === true,
      )
      .reverse()
      .slice(0, MAX_VISIBLE_RUNS)
      .map((run) => ({
        id: run.runId,
        label: copy(this.labels.get(scope(orgId, run.runId)) ?? null),
      }));
  }

  async storeFollowups(
    orgId: string,
    runId: string,
    followups: readonly Followup[],
  ): Promise<FollowupsWrite | null> {
    const run = this.runs.get(scope(orgId, runId));
    if (run === undefined) return null;
    if (run.status !== 'completed') return { outcome: 'not_completed' };
    if (run.followups !== undefined) {
      return { outcome: 'kept', followups: copy(run.followups) };
    }
    run.followups = copy([...followups]);
    return { outcome: 'stored', followups: copy(run.followups) };
  }

  async readEnding(orgId: string, runId: string): Promise<RunEnding | null> {
    const run = this.runs.get(scope(orgId, runId));
    if (run === undefined || !TERMINAL_RUN_STATUSES.has(run.status)) {
      return null;
    }
    return {
      run: copy(run),
      label: copy(this.labels.get(scope(orgId, runId)) ?? null),
    };
  }

  async historyLabels(run: HarnessRun): Promise<Array<TurnLabel | null>> {
    const stored = this.runs.get(scope(run.orgId, run.runId));
    if (stored === undefined) return [null];
    const seen = seenRuns(this.audiences.get(scope(run.orgId, run.runId)));
    const labelOf = (runId: string): TurnLabel | null =>
      copy(this.labels.get(scope(run.orgId, runId)) ?? null);
    const latest = this.rootRuns(run.orgId, run.sessionId)
      .filter(
        (earlier) =>
          earlier.runId !== run.runId &&
          earlier.createdAt <= stored.createdAt &&
          (seen === null || seen.own.includes(earlier.runId)),
      )
      .pop();
    return [
      ...(latest === undefined ? [] : [labelOf(latest.runId)]),
      ...(seen?.visible ?? []).map(labelOf),
    ];
  }

  /** The session's root runs, oldest first. */
  private rootRuns(orgId: string, sessionId: string): HarnessRun[] {
    return [...this.runs.values()]
      .filter(
        (run) =>
          run.orgId === orgId &&
          run.sessionId === sessionId &&
          childOf(run) === null,
      )
      .sort((a, b) => a.createdAt - b.createdAt);
  }

  async control(
    command: ControlRun,
    journal: readonly PreparedJournalAppend[] = [],
    transcript?: RunTranscript,
    fence?: CommitFence,
  ): Promise<CommitReceipt> {
    return this.oneWriter(() =>
      this.controlNow(command, journal, transcript, fence),
    );
  }

  /**
   * Commits and controls one at a time. Each awaits its transcript write
   * between its receipt check and its write; without this, two identical
   * commands would both pass the check, where Mongo's transaction makes the
   * second retry and replay.
   */
  private oneWriter<T>(write: () => Promise<T>): Promise<T> {
    const result = this.writes.then(write);
    this.writes = result.catch(() => undefined);
    return result;
  }

  private async controlNow(
    command: ControlRun,
    journal: readonly PreparedJournalAppend[] = [],
    transcript?: RunTranscript,
    fence?: CommitFence,
  ): Promise<CommitReceipt> {
    const now = this.clock.now();
    const receiptKey = scope(command.orgId, command.runId, command.commandId);
    const canonical = canonicalJson(command);
    const previous = this.commitReceipts.get(receiptKey);
    if (previous) {
      if (previous.payload !== canonical) {
        throw new HarnessConflictError(
          'IDEMPOTENCY_CONFLICT',
          'The command ID was already used with a different transition',
        );
      }
      return { ...copy(previous.receipt), replayed: true };
    }

    const run = this.requireRun(command.orgId, command.runId);
    assertControlCurrent(run, command);
    await fence?.hold(undefined);

    let eventType: string;
    let cancelledJoin: PreparedJoin | null = null;
    if (command.action === 'resolve') {
      const ids = controlResolutions(command).map((r) => r.interactionId);
      if (ids.length === 0) assertResolvable(run, '');
      for (const interactionId of ids) assertResolvable(run, interactionId);
      const resumes = RESUMABLE_RUN_STATUSES.has(run.status);
      if (resumes && run.absoluteDeadlineAt <= now) {
        throw new HarnessConflictError(
          'DEADLINE_EXCEEDED',
          'Run deadline has elapsed',
        );
      }
      for (const interactionId of ids) {
        run.openInteractions = withoutInteraction(
          run.openInteractions,
          interactionId,
        );
      }
      const needs = resolutionFeatures(controlResolutions(command));
      if (needs.length > 0) {
        run.requires = withFeature(requirementsOf(run), ...needs);
      }
      eventType = 'interaction_resolved';
      // A person answered: they wait on it now, whether it re-queues or its worker still holds it (G-45).
      Object.assign(run, priorityFields(afterApproval(priorityClassOf(run))));
      if (resumes) {
        this.giveWorkBack(run, now);
        run.status = 'queued';
        run.workerId = null;
        run.attemptId = null;
        run.leaseExpiresAt = null;
        run.notBefore = now;
        eventType = 'run_resumed';
        this.insertOutbox(
          command.orgId,
          {
            commandId: command.dispatchCommandId,
            runId: command.runId,
            kind: 'run_resume',
          },
          now,
        );
      }
    } else if (command.action === 'suspend') {
      assertRetirable(run);
      // A held run's working time waits with it, as a run waiting for a person's does (FIX-P1-F3).
      run.workLeftMs = Math.max(0, run.absoluteDeadlineAt - now);
      run.absoluteDeadlineAt = Math.max(now, command.holdUntil ?? now);
      run.status = 'paused';
      run.suspendedFor = RETIRED;
      // The attempt was taken back, not lost: it counts toward no crash loop (the worker quarantines on attempts).
      if (run.attemptId !== null) {
        run.attempts = Math.max(0, (run.attempts ?? 0) - 1);
      }
      run.workerId = null;
      run.attemptId = null;
      run.leaseExpiresAt = null;
      // ADR-RUN-01 D4: the parents joined on it hold their working time with it (WAITCHILD).
      this.holdAncestors(run, now);
      eventType = 'run_suspended';
    } else if (command.action === 'resume') {
      if (command.cause !== undefined) assertRetired(run);
      if (!RESUMABLE_RUN_STATUSES.has(run.status)) {
        throw new HarnessConflictError(
          'INVALID_TRANSITION',
          'Run cannot be resumed',
        );
      }
      if (run.absoluteDeadlineAt <= now) {
        throw new HarnessConflictError(
          'DEADLINE_EXCEEDED',
          'Run deadline has elapsed',
        );
      }
      this.giveWorkBack(run, now);
      Object.assign(run, priorityFields(afterApproval(priorityClassOf(run))));
      if (command.cause !== undefined) run.suspendedFor = null;
      run.status = 'queued';
      run.workerId = null;
      run.attemptId = null;
      run.leaseExpiresAt = null;
      run.notBefore = now;
      eventType = 'run_resumed';
      this.insertOutbox(
        command.orgId,
        {
          commandId: command.dispatchCommandId,
          runId: command.runId,
          kind: 'run_resume',
        },
        now,
      );
    } else {
      if (TERMINAL_RUN_STATUSES.has(run.status)) {
        throw new HarnessConflictError(
          'INVALID_TRANSITION',
          'Terminal run cannot be cancelled',
        );
      }
      const stop = operatorStopAfterCancel(run, command, now);
      if (stop !== undefined) run.operatorStop = stop;
      if (WORKER_OWNED_RUN_STATUSES.has(run.status)) {
        run.status = 'cancelling';
        eventType = 'run_cancellation_requested';
        this.insertOutbox(
          command.orgId,
          {
            commandId: command.dispatchCommandId,
            runId: command.runId,
            kind: 'run_cancel',
          },
          now,
        );
      } else {
        await this.saveTranscript(run, transcript, 'cancelled');
        cancelledJoin = this.prepareJoin(run, { status: 'cancelled' });
        run.status = 'cancelled';
        run.workerId = null;
        run.attemptId = null;
        run.leaseExpiresAt = null;
        this.activeSessions.delete(scope(run.orgId, run.sessionId));
        eventType = 'run_cancelled';
      }
    }

    run.revision += 1;
    run.updatedAt = now;
    for (const append of journal) this.journal.appendNow(append);
    if (cancelledJoin !== null) this.applyJoin(run, cancelledJoin, now);
    const eventScope = scope(run.orgId, run.sessionId);
    const storedEvents = this.events.get(eventScope) ?? [];
    const eventSeq = storedEvents.length + 1;
    this.events.set(eventScope, [
      ...storedEvents,
      {
        orgId: run.orgId,
        sessionId: run.sessionId,
        runId: run.runId,
        eventSeq,
        commitSeq: run.revision,
        type: eventType,
        data: {},
        createdAt: now,
      },
    ]);

    const receipt: CommitReceipt = {
      commandId: command.commandId,
      run: copy(run),
      firstEventSeq: eventSeq,
      lastEventSeq: eventSeq,
    };
    this.commitReceipts.set(receiptKey, {
      payload: canonical,
      receipt: copy(receipt),
    });
    return receipt;
  }

  async get(orgId: string, runId: string): Promise<HarnessRun | null> {
    const run = this.runs.get(scope(orgId, runId));
    return run !== undefined ? copy(run) : null;
  }

  async completedRootChildren(
    orgId: string,
    sessionId: string,
    limit: number,
  ): Promise<HarnessRun[]> {
    return [...this.runs.values()]
      .filter(
        (run) =>
          run.orgId === orgId &&
          run.sessionId === sessionId &&
          run.parent?.depth === 1 &&
          run.status === 'completed',
      )
      .sort((a, b) =>
        b.updatedAt !== a.updatedAt
          ? b.updatedAt - a.updatedAt
          : a.runId.localeCompare(b.runId),
      )
      .slice(0, limit)
      .map(copy);
  }

  async pendingApprovalRuns(
    orgId: string,
    filter: PendingApprovalFilter,
  ): Promise<HarnessRun[]> {
    return [...this.runs.values()]
      .filter(
        (run) =>
          run.orgId === orgId &&
          DECIDABLE_RUN_STATUSES.has(run.status) &&
          (run.openInteractions?.length ?? 0) > 0 &&
          (filter.actorId === undefined || run.actorId === filter.actorId) &&
          (filter.agentKey === undefined || run.agentKey === filter.agentKey),
      )
      .sort((a, b) =>
        b.updatedAt !== a.updatedAt
          ? b.updatedAt - a.updatedAt
          : a.runId.localeCompare(b.runId),
      )
      .slice(0, filter.limit)
      .map(copy);
  }

  async unansweredQuestionRuns(
    orgId: string,
    filter: UnansweredQuestionFilter,
  ): Promise<HarnessRun[]> {
    const latest = new Map<string, HarnessRun>();
    for (const run of this.runs.values()) {
      if (run.orgId !== orgId || run.parent !== undefined) continue;
      const seen = latest.get(run.sessionId);
      if (seen === undefined || run.createdAt > seen.createdAt) {
        latest.set(run.sessionId, run);
      }
    }
    return [...latest.values()]
      .filter(
        (run) =>
          run.actorId === filter.actorId &&
          run.status === 'completed' &&
          run.outcome === 'needs_input' &&
          (filter.agentKey === undefined || run.agentKey === filter.agentKey),
      )
      .sort((a, b) =>
        b.updatedAt !== a.updatedAt
          ? b.updatedAt - a.updatedAt
          : a.runId.localeCompare(b.runId),
      )
      .slice(0, filter.limit)
      .map(copy);
  }

  async admitChild(command: AdmitChildCommand): Promise<ChildAdmission> {
    const now = this.clock.now();
    const parent = this.requireOwnedAttempt(
      { orgId: command.orgId, runId: command.parentRunId, ...command.attempt },
      now,
    );
    if (parent.status !== 'running') {
      throw new HarnessConflictError(
        'STALE_ATTEMPT',
        'Only a running attempt admits children',
      );
    }
    const runKey = scope(command.orgId, command.childRunId);
    const existing = this.runs.get(runKey);
    if (existing !== undefined) {
      if (
        existing.payloadHash !== command.requestHash ||
        existing.parent?.runId !== parent.runId
      ) {
        throw new HarnessConflictError(
          'IDEMPOTENCY_CONFLICT',
          'This invocation already admitted a different child',
        );
      }
      return { run: copy(existing), created: false };
    }
    assertChildBound(parent, command.maxChildren);
    assertPinCurrent(command, this.controlPlaneHeads(command.orgId));
    if (this.budgets !== null && command.budget !== undefined) {
      this.budgets.admitChildNow(
        command.orgId,
        command.childRunId,
        command.budget,
      );
    }
    const sessionScope = scope(parent.orgId, parent.sessionId);
    const child = childRunDocument(parent, command, now, this.runKeys);
    child.requires = {
      ...withFeature(
        admissionRequirements(
          child,
          this.journalFeatures.get(sessionScope) ?? [],
        ),
        ...childFeatures(command.pinned, command.roleModel),
      ),
      schemaVersion: command.pinned.spec.schemaVersion,
    };
    // Validated by the caller; applied with no await in between, as one transaction.
    this.runs.set(runKey, child);
    this.pins.set(runKey, copy(command.pinned));
    this.vias.set(runKey, command.via);
    if (command.resolved !== undefined) {
      this.resolutions.set(runKey, copy(command.resolved));
    }
    this.journal.appendNow(command.childJournal);
    this.journal.appendNow(command.parentJournal);
    parent.children = {
      spawned: (parent.children?.spawned ?? 0) + 1,
      open: (parent.children?.open ?? 0) + 1,
    };
    parent.requires = withFeature(
      requirementsOf(parent),
      ...parentFeatures(command.pinned, command.roleModel),
    );
    this.journalFeatures.set(sessionScope, [
      ...new Set<WorkerFeature>([
        ...(this.journalFeatures.get(sessionScope) ?? []),
        ...sessionChildFeatures(command.roleModel),
      ]),
    ]);
    this.insertOutbox(
      command.orgId,
      {
        commandId: `child:${command.childRunId}`,
        runId: command.childRunId,
        kind: 'run_ready',
      },
      now,
    );
    return { run: copy(child), created: true };
  }

  async openChildren(
    orgId: string,
    parentRunId: string,
  ): Promise<HarnessRun[]> {
    return [...this.runs.values()]
      .filter(
        (run) =>
          run.orgId === orgId &&
          run.parent?.runId === parentRunId &&
          !TERMINAL_RUN_STATUSES.has(run.status),
      )
      .sort((left, right) => left.createdAt - right.createdAt)
      .map(copy);
  }

  async orphanedChildren(limit: number): Promise<HarnessRun[]> {
    return [...this.runs.values()]
      .filter((run) => {
        if (run.parent === undefined || TERMINAL_RUN_STATUSES.has(run.status)) {
          return false;
        }
        const parent = this.runs.get(scope(run.orgId, run.parent.runId));
        return parent === undefined || TERMINAL_RUN_STATUSES.has(parent.status);
      })
      .sort((left, right) => left.createdAt - right.createdAt)
      .slice(0, Math.max(limit, 1))
      .map(copy);
  }

  /** What a child ending writes on its parent's side; null for a root run. */
  private prepareJoin(
    child: HarnessRun,
    ending: ChildEnding,
  ): PreparedJoin | null {
    if (child.parent === undefined) return null;
    const parent = this.runs.get(scope(child.orgId, child.parent.runId));
    if (parent === undefined) return null;
    const open = Math.max(0, (parent.children?.open ?? 0) - 1);
    if (TERMINAL_RUN_STATUSES.has(parent.status)) {
      return { parent, open, appends: [], wakes: false };
    }
    const wakes = joinWakes(parent, open);
    return {
      parent,
      open,
      appends: [
        nodeJournalAppend(parent, [
          childCompletedEvent(child, ending),
          ...(wakes ? [childDoneResumedEvent()] : []),
        ]),
      ],
      wakes,
    };
  }

  /** The parent's side of a child ending, in the same synchronous step. */
  private applyJoin(child: HarnessRun, join: PreparedJoin, now: number): void {
    const parent = join.parent;
    parent.children = {
      spawned: parent.children?.spawned ?? 0,
      open: join.open,
    };
    for (const append of join.appends) this.journal.appendNow(append);
    const released = ancestorReleased(parent, child.runId, now, join.wakes);
    Object.assign(parent, released.fields);
    if (released.continues) this.releaseAncestors(parent, now);
    if (!join.wakes) return;
    parent.status = 'queued';
    parent.suspendedFor = null;
    parent.workerId = null;
    parent.attemptId = null;
    parent.leaseExpiresAt = null;
    parent.notBefore = now;
    parent.revision += 1;
    parent.updatedAt = now;
    this.insertOutbox(
      parent.orgId,
      joinWakeCommand(parent.runId, child.runId),
      now,
    );
  }

  async getActive(
    orgId: string,
    sessionId: string,
  ): Promise<HarnessRun | null> {
    const runKey = this.activeSessions.get(scope(orgId, sessionId));
    if (runKey === undefined) return null;
    const run = this.runs.get(runKey);
    return run !== undefined ? copy(run) : null;
  }

  async readEvents(
    orgId: string,
    sessionId: string,
    after: number,
    limit: number,
  ): Promise<HarnessEvent[]> {
    const stored = this.events.get(scope(orgId, sessionId)) ?? [];
    return copy(
      stored.filter((event) => event.eventSeq > after).slice(0, limit),
    );
  }

  async sweep(limit: number, transcripts?: SweptTranscripts): Promise<number> {
    const now = this.clock.now();
    const bound = Math.min(Math.max(limit, 1), 100);
    const leaseExpired = [...this.runs.values()]
      .filter((run) => WORKER_OWNED_RUN_STATUSES.has(run.status))
      .filter((run) => run.leaseExpiresAt !== null && run.leaseExpiresAt <= now)
      .sort(
        (left, right) =>
          (left.leaseExpiresAt as number) - (right.leaseExpiresAt as number),
      )
      .slice(0, bound);
    const pastDeadline = [...this.runs.values()]
      .filter((run) => IDLE_RUN_STATUSES.has(run.status))
      .filter((run) => run.absoluteDeadlineAt <= now)
      .sort((left, right) => left.absoluteDeadlineAt - right.absoluteDeadlineAt)
      .slice(0, bound - leaseExpired.length);

    let swept = 0;
    for (const run of leaseExpired) {
      const before = copy(run);
      const wasCancelling = run.status === 'cancelling';
      const deadlineElapsed = run.absoluteDeadlineAt <= now;
      // C4.10 `recovery.autoResume` off: the lost worker's run ends; the person sends again.
      const lost =
        !wasCancelling && !deadlineElapsed && !resumesAfterLostWorker(run);
      if (!wasCancelling && (deadlineElapsed || lost)) {
        const failure: SweptFailure = lost ? WORKER_LOST : 'deadline_exceeded';
        const data = lost ? { reason: WORKER_LOST } : {};
        if (await this.expireFailed(run, data, now, transcripts, failure)) {
          swept += 1;
          transcripts?.expired?.(before, 'failed', failure);
        }
        continue;
      }
      // A lapsed cancel ends the run here: the calls its attempt left open end with it (EV-06).
      const revision = run.revision;
      const open = wasCancelling
        ? ((await transcripts?.openCalls?.(copy(run))) ?? [])
        : [];
      if (run.revision !== revision) continue;
      this.expire(
        run,
        wasCancelling ? 'cancelled' : 'queued',
        wasCancelling ? 'run_cancelled' : 'run_recovery_queued',
        {},
        now,
        sweptJournal(run, wasCancelling ? 'cancelled' : 'queued', open),
      );
      swept += 1;
      transcripts?.expired?.(
        before,
        wasCancelling ? 'cancelled' : 'queued',
        wasCancelling ? 'cancel_lease_expired' : 'lease_expired',
      );
    }
    for (const run of pastDeadline) {
      // A child ended earlier in this pass may have moved it (its join woke it, or gave its time back).
      if (run.absoluteDeadlineAt > now || !IDLE_RUN_STATUSES.has(run.status)) {
        continue;
      }
      const before = copy(run);
      const data = { reason: 'deadline_exceeded' };
      if (await this.expireFailed(run, data, now, transcripts)) {
        swept += 1;
        transcripts?.expired?.(before, 'failed', 'deadline_exceeded');
      }
    }
    return swept;
  }

  /**
   * A person answered, or the run was resumed: its working deadline counts
   * again from now (FIX-P1-F3), and so do its joined parents' unless another
   * child of theirs still waits (WAITCHILD).
   */
  private giveWorkBack(run: HarnessRun, now: number): void {
    const left = heldWorkLeft(run);
    if (left === null) return;
    run.absoluteDeadlineAt = now + left;
    run.workLeftMs = null;
    this.releaseAncestors(run, now);
  }

  private parentRun(run: HarnessRun): HarnessRun | undefined {
    return run.parent != null
      ? this.runs.get(scope(run.orgId, run.parent.runId))
      : undefined;
  }

  /** Whether the run's root is a scheduled firing, whose tree keeps the schedule's deadline (AP-1). */
  private treeScheduled(run: HarnessRun): boolean {
    let root = run;
    for (
      let up = this.parentRun(root);
      up !== undefined;
      up = this.parentRun(root)
    ) {
      root = up;
    }
    return this.triggers.has(scope(root.orgId, root.runId));
  }

  /** The deadlines of the run's children that wait for a person (WAITCHILD). */
  private childDeadlines(run: HarnessRun): number[] {
    return (run.waitingChildren ?? []).flatMap((childRunId) => {
      const child = this.runs.get(scope(run.orgId, childRunId));
      return child !== undefined && heldWorkLeft(child) !== null
        ? [child.absoluteDeadlineAt]
        : [];
    });
  }

  /** `run` began holding its time for a person: each parent joined on it holds its own (WAITCHILD). */
  private holdAncestors(run: HarnessRun, now: number): void {
    let child = run;
    for (
      let up = this.parentRun(child);
      up !== undefined;
      up = this.parentRun(child)
    ) {
      if (TERMINAL_RUN_STATUSES.has(up.status)) return;
      const step = ancestorHeld(up, child.runId, child.absoluteDeadlineAt, now);
      Object.assign(up, step.fields);
      if (!step.continues) return;
      child = up;
    }
  }

  /** `run` stopped holding its time for a person: its parents get theirs back once no child of theirs waits. */
  private releaseAncestors(run: HarnessRun, now: number): void {
    let child = run;
    for (
      let up = this.parentRun(child);
      up !== undefined;
      up = this.parentRun(child)
    ) {
      const step = ancestorReleased(up, child.runId, now);
      Object.assign(up, step.fields);
      if (!step.continues) return;
      child = up;
    }
  }

  /** Fails a run at its deadline, its transcript written before anything mutates. */
  private async expireFailed(
    run: HarnessRun,
    eventData: Record<string, unknown>,
    now: number,
    transcripts: SweptTranscripts | undefined,
    failure: SweptFailure = 'deadline_exceeded',
  ): Promise<boolean> {
    const revision = run.revision;
    const open = (await transcripts?.openCalls?.(copy(run))) ?? [];
    if (run.revision !== revision) return false;
    const appending = sweptJournalEvents(run, 'failed', open, failure);
    const journal = sweptJournal(run, 'failed', open, failure);
    if (
      transcripts === undefined ||
      this.transcripts === null ||
      appending.length === 0
    ) {
      this.expire(run, 'failed', 'run_failed', eventData, now, journal);
      return true;
    }
    const transcript = await transcripts.project(copy(run), appending);
    // Another writer moved the run while it was projected; its CAS lost.
    if (run.revision !== revision) return false;
    let outcome: SweptTranscriptOutcome | null = null;
    let cause: unknown;
    if (transcript !== undefined) {
      try {
        outcome = (await this.transcripts.record(copy(run), transcript))
          ? 'written'
          : 'session_missing';
      } catch (error) {
        outcome = 'write_failed';
        cause = error;
      }
      if (outcome === 'written') this.keepLabel(run, transcript);
    }
    this.expire(run, 'failed', 'run_failed', eventData, now, journal);
    if (transcript !== undefined && outcome !== null) {
      transcripts.settled(copy(run), transcript, outcome, cause);
    }
    return true;
  }

  private expire(
    run: HarnessRun,
    nextStatus: RunStatus,
    eventType: string,
    eventData: Record<string, unknown>,
    now: number,
    journal: readonly PreparedJournalAppend[] = sweptJournal(run, nextStatus),
  ): void {
    const previousFence = run.fence;
    const join = TERMINAL_RUN_STATUSES.has(nextStatus)
      ? this.prepareJoin(run, sweptEnding(nextStatus, eventData))
      : null;
    for (const append of journal) this.journal.appendNow(append);
    run.status = nextStatus;
    run.workerId = null;
    run.attemptId = null;
    run.leaseExpiresAt = null;
    if (nextStatus === 'queued') run.notBefore = now;
    run.revision += 1;
    run.updatedAt = now;
    if (join !== null) this.applyJoin(run, join, now);
    if (TERMINAL_RUN_STATUSES.has(nextStatus)) {
      this.activeSessions.delete(scope(run.orgId, run.sessionId));
    } else {
      this.insertOutbox(
        run.orgId,
        {
          commandId: `recover:${run.runId}:${String(previousFence)}:${String(run.revision)}`,
          runId: run.runId,
          kind: 'run_resume',
        },
        now,
      );
    }
    this.appendEvent(run, run.revision, eventType, now, eventData);
  }

  private appendEvent(
    run: HarnessRun,
    commitSeq: number,
    type: string,
    now: number,
    data: Record<string, unknown> = {},
  ): number {
    const eventScope = scope(run.orgId, run.sessionId);
    const stored = this.events.get(eventScope) ?? [];
    const eventSeq = stored.length + 1;
    this.events.set(eventScope, [
      ...stored,
      {
        orgId: run.orgId,
        sessionId: run.sessionId,
        runId: run.runId,
        eventSeq,
        commitSeq,
        type,
        data,
        createdAt: now,
      },
    ]);
    return eventSeq;
  }

  private requireRun(orgId: string, runId: string): HarnessRun {
    const run = this.runs.get(scope(orgId, runId));
    if (!run) throw new HarnessNotFoundError();
    return run;
  }

  private requireIndexedRun(runKey: string): HarnessRun {
    const run = this.runs.get(runKey);
    if (run === undefined) {
      throw new Error('Active-session index references a missing run');
    }
    return run;
  }

  private requireOwnedAttempt(
    command: Pick<
      RenewAttempt,
      'orgId' | 'runId' | 'workerId' | 'attemptId' | 'fence'
    >,
    now: number,
  ): HarnessRun {
    const run = this.requireRun(command.orgId, command.runId);
    if (
      !WORKER_OWNED_RUN_STATUSES.has(run.status) ||
      run.workerId !== command.workerId ||
      run.attemptId !== command.attemptId ||
      run.fence !== command.fence ||
      run.leaseExpiresAt === null ||
      run.leaseExpiresAt <= now
    ) {
      throw new HarnessConflictError(
        'STALE_ATTEMPT',
        'Worker no longer owns this run',
      );
    }
    if (run.absoluteDeadlineAt <= now) {
      throw new HarnessConflictError(
        'DEADLINE_EXCEEDED',
        'Run deadline has elapsed',
      );
    }
    return run;
  }

  private boundedLease(
    now: number,
    durationMs: number,
    absoluteDeadlineAt: number,
  ): number {
    if (!Number.isSafeInteger(durationMs) || durationMs <= 0) {
      throw new HarnessConflictError(
        'DEADLINE_EXCEEDED',
        'Lease duration must be positive',
      );
    }
    return Math.min(now + durationMs, absoluteDeadlineAt);
  }

  private insertOutbox(
    orgId: string,
    item: { commandId: string; runId: string; kind: DispatchRecord['kind'] },
    now: number,
  ): void {
    this.dispatches.push({ ...copy(item), orgId, createdAt: now });
  }
}

/** The in-memory store's stand-in for the conversation module's transcript write. */
export interface TranscriptSink {
  record(run: HarnessRun, transcript: RunTranscript): Promise<boolean>;
  /** Whether the run's conversation still exists (QA21-F4); a sink without the notion keeps every one. */
  sessionExists?(run: HarnessRun): Promise<boolean>;
}
