import { eq, idOperator } from '../../id-filter-guard/mongo';
import { mongoTx } from '../durable-transaction/mongo';
import { asMongoSession } from '../durable-transaction/mongo';
import type { Tx } from '../../uow';
import { REGION_PATH } from '../schema/harness-model.schema';
import {
  assertRetirable,
  assertRetired,
} from '../../../../modules/agent-harness/domain/operator-suspension';
import { RETIRED } from '../../../../modules/agent-harness/domain/operator-controls';
import type { RunParent } from '../../../../modules/agent-harness/domain/answered-run';
import { recordedRunFieldsOf } from '../../../../modules/agent-harness/domain/recorded-run-fields';
import type { ResolvedRunConfig } from '../../../../modules/control-plane';
import type { CommitFence } from '../../../../modules/agent-harness/ports/commit-fence';
import { ClientSession } from 'mongoose';
import type { ObjectId } from 'mongodb';
import type { PinnedEngineProfile } from '../../../../modules/agent-harness/contracts/generated/core/engine-profile';
import { consumedInputs } from '../../../../modules/agent-harness/domain/steering';
import {
  assertControlCurrent,
  assertResolvable,
  commutes,
  assertSuspendable,
  DECIDABLE_RUN_STATUSES,
  requestedInteractions,
  resolutionFeatures,
  RESUMABLE_RUN_STATUSES,
} from '../../../../modules/agent-harness/domain/interactions';
import {
  canonicalJson,
  canonicalSha256,
} from '../../../../modules/agent-harness/contracts/canonical-json';
import {
  HarnessConflictError,
  ConversationDeletedError,
  HarnessNotFoundError,
  TranscriptProjectionError,
} from '../../../../modules/agent-harness/domain/errors';
import {
  attemptOwnsRun,
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
  entersPersonWait,
  startsWaitingForPerson,
  heldWorkLeft,
  joinDeadline,
  JournalRange,
  journalStreamId,
  HarnessEvent,
  IDLE_RUN_STATUSES,
  HarnessRun,
  nodeJournalAppend,
  ReleaseAttempt,
  RenewAttempt,
  runClaimedEvent,
  resumedNotices,
  RunStatus,
  sweptJournalEvents,
  resumesAfterLostWorker,
  WORKER_LOST,
  type SweptFailure,
  TERMINAL_RUN_STATUSES,
  transitionEffects,
  validateCommitBatch,
  waitForPersonMs,
  waitsForPerson,
  WORKER_OWNED_RUN_STATUSES,
  endsWithoutTranscript,
} from '../../../../modules/agent-harness/domain/execution';
import {
  afterApproval,
  priorityClassOf,
  priorityFields,
  priorityRank,
  type ClaimOrder,
} from '../../../../modules/agent-harness/domain/priority-class';
import {
  Followup,
  FollowupsWrite,
} from '../../../../modules/agent-harness/domain/followups';
import {
  PreparedJournalAppend,
  PreparedJournalEvent,
} from '../../../../modules/agent-harness/domain/journal';
import {
  PinnedRunSpec,
  RunVia,
} from '../../../../modules/agent-harness/domain/run-spec';
import { JournalStore } from '../../../../modules/agent-harness/ports/journal-store';
import {
  ActorRuns,
  ClaimSubject,
  ExecutionStore,
  OpenRun,
  OpenRunFilter,
  OperatorRuns,
  PendingApprovalFilter,
  UnansweredQuestionFilter,
  RunEnding,
  StoredReceipt,
  SweptTranscriptOutcome,
  SweptTranscripts,
  StrandedRun,
  WaitingRun,
} from '../../../../modules/agent-harness/ports/execution-store';
import {
  ConversationWriter,
  RunTranscript,
} from '../../../../modules/agent-harness/ports/conversation-writer';
import { DispatchOutbox } from '../../../../modules/agent-harness/ports/dispatch-outbox';
import type { ApproverNoticeOutbox } from '../../../../modules/agent-harness/ports/approval-links';
import { approverNoticeId } from '../../../../modules/agent-harness/domain/approval-links';
import {
  RunAudience,
  seenRuns,
} from '../../../../modules/agent-harness/domain/session-audience';
import {
  parseTurnLabel,
  TurnLabel,
} from '../../../../modules/agent-harness/domain/turn-labels';
import {
  AgentCommandReceiptModel,
  AgentEventCounterModel,
  AgentEventModel,
  AgentRunModel,
  runFromDocument,
  storedHistoryRef,
} from './schema/execution.schema';
import { MongoJournalStore } from '../journal/mongo';
import type { RunLists } from '../../../../modules/agent-harness/ports/run-lists';
import {
  type EndedRunFact,
  type ListedJob,
  type ListedRun,
  listedCarry,
  listedEngine,
  MAX_SESSION_OPEN_RUNS,
  type PersonRunQuery,
  type RunListState,
  STATE_STATUSES,
} from '../../../../modules/agent-harness/domain/run-lists';
import { moveWorkspaceHead } from '../workspace-head-cas/mongo';
import {
  inMongoTransaction,
  MAJORITY_WRITE,
} from '../durable-transaction/mongo';
import { createInTransaction } from '../transaction-writes/mongo';
import { MongoTime, mongoServerTime } from '../time/mongo';
import {
  canRunExpr,
  claimedRequiresExpr,
  compatibleExpr,
  placedExpr,
  withFeatureExpr,
} from '../worker-compat-expr/mongo';
import { ChildJoinDeps, joinParent } from '../child-runs/mongo';
import {
  holdAncestors,
  releaseAncestors,
  rootScheduled,
  waitingChildDeadlines,
} from '../person-wait/mongo';
import {
  endingOf,
  sweptEnding,
} from '../../../../modules/agent-harness/domain/child-runs';
import {
  ClaimingWorker,
  missingFor,
  requirementsOf,
  unattemptedRelease,
  WorkerFeature,
  workerIncompatible,
} from '../../../../modules/agent-harness/domain/worker-compat';

const plain = <T>(value: T): T => structuredClone(value);

const isTransientTransactionError = (error: unknown): boolean => {
  if (typeof error !== 'object' || error === null) return false;
  const labelled = error as {
    hasErrorLabel?: (label: string) => boolean;
    errorLabels?: unknown;
  };
  return typeof labelled.hasErrorLabel === 'function'
    ? labelled.hasErrorLabel('TransientTransactionError')
    : Array.isArray(labelled.errorLabels) &&
        labelled.errorLabels.includes('TransientTransactionError');
};

const commandHash = (command: unknown): string => canonicalSha256(command);

const readPin = (stored: unknown): PinnedRunSpec | null =>
  typeof stored === 'string' ? (JSON.parse(stored) as PinnedRunSpec) : null;

const sweptJournal = (
  run: HarnessRun,
  nextStatus: RunStatus,
  open: readonly string[] = [],
  errorType: SweptFailure = 'deadline_exceeded',
): PreparedJournalAppend[] => {
  const events = sweptJournalEvents(run, nextStatus, open, errorType);
  return events.length > 0 ? [nodeJournalAppend(run, events)] : [];
};

/** `session_missing`: the transcript's conversation is gone; only its status was set. */
type Expiry = 'lost' | 'expired' | 'session_missing';

const activeSessionKey = (orgId: string, sessionId: string): string =>
  JSON.stringify([orgId, sessionId]);

function isDuplicateKeyError(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    'code' in error &&
    error.code === 11000
  );
}

/** Where a commit's transaction read finds its command's receipt next to the run (`runWithReceipt`). */
const PRIOR_RECEIPT = '__priorReceipt';

/** The stored run as the driver hands a commit's update back: the fields the commit reads after it. */
type CommittedRun = Record<string, unknown> & {
  _id: ObjectId;
  updatedAt: number;
  revision: number;
  workLeftMs?: number | null;
};

/** A lean read skips schema defaults: a run admitted before `via` was recorded has none, and came in with a session. */
const viaOf = (via: RunVia | undefined): RunVia => via ?? 'session';

export class MongoExecutionStore
  implements ExecutionStore, ActorRuns, OperatorRuns, RunLists
{
  constructor(
    private readonly conversations: ConversationWriter<Tx>,
    private readonly dispatches: DispatchOutbox<Tx>,
    private readonly time: MongoTime = mongoServerTime,
    private readonly journal: JournalStore<Tx> = new MongoJournalStore(time),
    /** WP56-OUTBOX: a wait's approver notices, written in the commit that makes the run wait. Absent: none. */
    private readonly approverNotices: ApproverNoticeOutbox<Tx> | null = null,
  ) {}

  private get joinDeps(): ChildJoinDeps {
    return {
      journal: this.journal,
      dispatches: this.dispatches,
      time: this.time,
    };
  }

  /**
   * A run that begins waiting for a person keeps its working time left and stays
   * answerable for the wait window (`personWaitDeadline`, in DB time).
   */
  private personWaitFields(): Record<string, unknown> {
    const now = this.time.expr();
    const deployment = waitForPersonMs();
    // C4.10 `interactions.waitS`: the run's pin may only shorten the deployment's window.
    const window = {
      $min: [
        deployment,
        { $ifNull: [{ $multiply: ['$personWaitS', 1000] }, deployment] },
      ],
    };
    return {
      workLeftMs: { $max: [0, { $subtract: ['$absoluteDeadlineAt', now] }] },
      absoluteDeadlineAt: {
        $max: ['$absoluteDeadlineAt', { $add: [now, window] }],
      },
    };
  }

  /** `$field > now` on DB time; false when the field is null. */
  private isAfterNow(field: string): Record<string, unknown> {
    return {
      $and: [
        { $isNumber: `$${field}` },
        { $gt: [`$${field}`, this.time.expr()] },
      ],
    };
  }

  private isNotAfterNow(field: string): Record<string, unknown> {
    return {
      $and: [
        { $isNumber: `$${field}` },
        { $lte: [`$${field}`, this.time.expr()] },
      ],
    };
  }

  /** Queued and due: `notBefore` absent (older runs) or not after DB time. */
  private isDue(): Record<string, unknown> {
    return {
      $or: [
        { $not: [{ $isNumber: '$notBefore' }] },
        { $lte: ['$notBefore', this.time.expr()] },
      ],
    };
  }

  private leaseUntil(durationMs: number): Record<string, unknown> {
    return {
      $min: ['$absoluteDeadlineAt', { $add: [this.time.expr(), durationMs] }],
    };
  }

  async readClaimSubject(
    orgId: string,
    runId: string,
  ): Promise<ClaimSubject | null> {
    const document = await AgentRunModel.findOne({
      orgId: eq(orgId),
      runId: eq(runId),
    })
      .read('primary')
      .lean();
    if (!document) return null;
    return {
      run: this.toRun(document),
      agentDefinition: plain(document.agentDefinition ?? null),
      engineProfile: plain(document.engineProfile ?? null),
      codeTaskAutoRoute: document.codeTaskAutoRoute === false ? false : null,
      repository: plain(document.repository ?? null),
      gates: plain(document.gates ?? null),
      answered: plain(document.answered ?? null),
      modelAccess: plain(document.modelAccess ?? null),
      via: viaOf(document.via),
      surface: document.surface ?? null,
      audience: plain(document.audience ?? null),
      trigger: plain(document.trigger ?? null),
      memory: plain(document.memory ?? null),
      pinned: readPin(document.pinned),
      history: storedHistoryRef(document),
      resolved:
        typeof document.resolved === 'string'
          ? (JSON.parse(document.resolved) as ResolvedRunConfig)
          : null,
    };
  }

  async claim(command: ClaimRun, pinning?: ClaimPinning): Promise<ClaimedRun> {
    this.validateLeaseDuration(command.leaseDurationMs);
    const worker = command.worker;
    const claimed = await inMongoTransaction(async (session) => {
      const run = await AgentRunModel.findOneAndUpdate(
        {
          orgId: eq(command.orgId),
          runId: eq(command.runId),
          $expr: {
            $and: [
              this.isAfterNow('absoluteDeadlineAt'),
              // Re-checked here: a requirement raised since the service read the run holds.
              ...(worker !== undefined ? [canRunExpr(worker)] : []),
              {
                $or: [
                  { $and: [{ $eq: ['$status', 'queued'] }, this.isDue()] },
                  {
                    $and: [
                      { $eq: ['$status', 'running'] },
                      this.isNotAfterNow('leaseExpiresAt'),
                    ],
                  },
                ],
              },
            ],
          },
        },
        [
          {
            $set: {
              status: 'running',
              workerId: { $literal: command.workerId },
              attemptId: { $literal: command.attemptId },
              leaseExpiresAt: this.leaseUntil(command.leaseDurationMs),
              fence: { $add: ['$fence', 1] },
              attempts: { $add: [{ $ifNull: ['$attempts', 0] }, 1] },
              // One $set stage reads the document as it was before the update.
              claimedFrom: {
                $cond: [
                  { $eq: ['$status', 'running'] },
                  'expired_lease',
                  'queued',
                ],
              },
              revision: { $add: ['$revision', 1] },
              updatedAt: this.time.expr(),
              ...(worker !== undefined
                ? {
                    requires: claimedRequiresExpr(worker),
                    attemptFeatures: { $literal: worker.features },
                    waitingForWorker: null,
                  }
                : {}),
            },
          },
        ],
        { updatePipeline: true, returnDocument: 'after', session },
      ).lean();
      if (run === null) return null;
      const claimedRun = this.toRun(run);
      const pinned =
        claimedRun.harnessMajor === 2
          ? await this.journalClaim(
              claimedRun,
              readPin(run.pinned),
              command,
              pinning,
              session,
            )
          : undefined;
      // Same snapshot as the fence bump: every commit of an older fence is
      // either counted here or can no longer land.
      const stream = journalStreamId(run);
      const lastSeq = await this.journal.lastSeq(
        command.orgId,
        [stream],
        mongoTx(session),
      );
      return {
        run: claimedRun,
        journal: { lastSeq },
        ...(pinned !== undefined ? { pinned } : {}),
      };
    });
    if (claimed === null) {
      await this.throwClaimFailure(command.orgId, command.runId, worker);
      throw new Error('Claim failure resolver returned unexpectedly');
    }
    return claimed;
  }

  /** `run.claimed`, and on the first claim the pin and `run.spec_pinned`. */
  private async journalClaim(
    run: HarnessRun,
    existing: PinnedRunSpec | null,
    command: ClaimRun,
    pinning: ClaimPinning | undefined,
    sessionHandle: Tx | ClientSession,
  ): Promise<PinnedRunSpec> {
    const session = asMongoSession(sessionHandle);
    const events: PreparedJournalEvent[] = [
      runClaimedEvent(
        run.attempts ?? run.fence,
        run.fence,
        command.leaseDurationMs,
      ),
      ...resumedNotices(run.attempts ?? 1),
    ];
    let pinned = existing;
    if (pinned === null) {
      if (pinning === undefined) {
        throw new HarnessConflictError(
          'SPEC_NOT_PINNED',
          'A harness v2 run needs a spec to pin on its first claim',
        );
      }
      // The pin's schemaVersion is what a Node must read to serve the run (RU-2g).
      const written = await AgentRunModel.updateOne(
        { orgId: run.orgId, runId: run.runId, pinned: null },
        {
          $set: {
            pinned: canonicalJson(pinning.pinned),
            'requires.schemaVersion': pinning.pinned.spec.schemaVersion,
          },
        },
        { session },
      );
      if (written.modifiedCount !== 1) {
        throw new HarnessConflictError(
          'SPEC_NOT_PINNED',
          'The run was pinned concurrently',
        );
      }
      pinned = pinning.pinned;
      events.push(pinning.specEvent);
    }
    await this.journal.append(
      {
        orgId: run.orgId,
        sessionId: run.sessionId,
        streamId: journalStreamId(run),
        runId: run.runId,
        attemptId: command.attemptId,
        fence: run.fence,
        writer: 'node',
        events,
      },
      mongoTx(session),
    );
    return pinned;
  }

  async nextClaimable(
    role: ClaimRole,
    tClaimMs: number,
    limit: number,
    worker?: ClaimingWorker,
    order: ClaimOrder = {},
  ): Promise<ClaimCandidate[]> {
    const candidates = await AgentRunModel.find(
      {
        ...this.dueFor(role),
        // `$not` keeps a run admitted before classes (no rank): it reads as interactive.
        ...(order.above !== undefined
          ? { priorityRank: { $not: { $gte: priorityRank(order.above) } } }
          : {}),
        $expr: {
          $and: [
            this.waitedFor(role, tClaimMs),
            this.isAfterNow('absoluteDeadlineAt'),
            // Filtered in the query, so runs this worker cannot run never
            // crowd out the ones it can.
            ...(worker !== undefined ? [canRunExpr(worker)] : []),
          ],
        },
      },
      { orgId: 1, runId: 1 },
    )
      .sort(
        order.floor
          ? { notBefore: 1, createdAt: 1 }
          : { priorityRank: 1, notBefore: 1, createdAt: 1 },
      )
      .limit(Math.max(limit, 1))
      .read('primary')
      .lean();
    return candidates.map((run) => ({ orgId: run.orgId, runId: run.runId }));
  }

  async markWaiting(
    role: ClaimRole,
    tClaimMs: number,
    limit: number,
    worker: ClaimingWorker,
  ): Promise<WaitingRun[]> {
    const hidden = await AgentRunModel.find(
      {
        ...this.dueFor(role),
        waitingForWorker: null,
        $expr: {
          $and: [
            this.waitedFor(role, tClaimMs),
            this.isAfterNow('absoluteDeadlineAt'),
            // A run of another region is another worker's, never one this worker leaves waiting.
            placedExpr(worker),
            { $not: [compatibleExpr(worker)] },
          ],
        },
      },
      { orgId: 1, runId: 1, sessionId: 1, harnessMajor: 1, requires: 1 },
    )
      .sort({ notBefore: 1, createdAt: 1 })
      .limit(Math.max(limit, 1))
      .read('primary')
      .lean();
    const marked: WaitingRun[] = [];
    for (const run of hidden) {
      const requires = requirementsOf(this.toRun(run));
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

  /** Served by the `{orgId, status, actorId}` index's org prefix; read on the primary, as a cancel must see the latest. */
  async activeRunsOfActor(
    orgId: string,
    actorId: string,
  ): Promise<HarnessRun[]> {
    const documents = await AgentRunModel.find({
      orgId,
      actorId,
      status: { $nin: [...TERMINAL_RUN_STATUSES] },
    })
      .read('primary')
      .lean();
    return documents.map((document) =>
      runFromDocument(document as unknown as Record<string, unknown>),
    );
  }

  // ADR-UI-01 D1: by RUNS_BY_ACTOR_CURSOR_INDEX (or RUNS_BY_ACTOR_AGENT_INDEX with an agent), newest first.
  async personRuns(query: PersonRunQuery): Promise<ListedRun[]> {
    if (query.limit <= 0) return [];
    const kind = query.kind ?? 'all';
    const states = query.states ?? [];
    const and: Record<string, unknown>[] = [];
    if (query.after !== undefined) {
      and.push({
        $or: [
          { createdAt: { $lt: query.after.createdAt } },
          {
            createdAt: query.after.createdAt,
            runId: idOperator({ $lt: query.after.runId }),
          },
        ],
      });
    }
    if (states.length > 0) and.push({ $or: states.map(mongoStatePredicate) });
    const created: Record<string, number> = {};
    if (query.createdFrom !== undefined) created['$gte'] = query.createdFrom;
    if (query.createdTo !== undefined) created['$lt'] = query.createdTo;
    const documents = await AgentRunModel.find({
      orgId: query.orgId,
      actorId: query.actorId,
      ...(query.agentKey !== undefined ? { agentKey: query.agentKey } : {}),
      ...(kind === 'root' ? { 'parent.runId': { $exists: false } } : {}),
      ...(kind === 'job' ? { 'parent.runId': { $exists: true } } : {}),
      ...(Object.keys(created).length > 0 ? { createdAt: created } : {}),
      ...(and.length > 0 ? { $and: and } : {}),
    })
      .sort({ createdAt: -1, runId: -1 })
      .limit(query.limit)
      .lean();
    return documents.map((document) => ({
      run: this.toRun(document),
      engine: listedEngine(
        document.engineProfile ?? null,
        readPin(document.pinned)?.spec ?? null,
      ),
    }));
  }

  // By RUNS_BY_SESSION_INDEX {orgId, sessionId}.
  async sessionJobs(
    orgId: string,
    sessionId: string,
    limit: number,
  ): Promise<ListedJob[]> {
    if (limit <= 0) return [];
    const documents = await AgentRunModel.find({
      orgId,
      sessionId,
      'parent.runId': { $exists: true },
    })
      .sort({ createdAt: -1, runId: -1 })
      .limit(limit)
      .lean();
    return documents.map((document) => {
      const spec = readPin(document.pinned)?.spec ?? null;
      return {
        run: this.toRun(document),
        engine: listedEngine(document.engineProfile ?? null, spec),
        continuesRunId: listedCarry(spec),
      };
    });
  }

  // By RUNS_BY_SESSION_INDEX {orgId, sessionId}.
  async openRunsOfSessions(
    orgId: string,
    sessionIds: readonly string[],
  ): Promise<HarnessRun[]> {
    if (sessionIds.length === 0) return [];
    const documents = await AgentRunModel.find({
      orgId,
      sessionId: { $in: [...sessionIds] },
      status: { $nin: [...TERMINAL_RUN_STATUSES] },
    })
      .sort({ createdAt: -1, runId: -1 })
      .limit(MAX_SESSION_OPEN_RUNS)
      .lean();
    return documents.map((document) => this.toRun(document));
  }

  // By RUNS_BY_SESSION_INDEX {orgId, sessionId}; one group per conversation.
  async lastEndedRuns(
    orgId: string,
    actorId: string,
    sessionIds: readonly string[],
  ): Promise<EndedRunFact[]> {
    if (sessionIds.length === 0) return [];
    const rows = await AgentRunModel.aggregate<{
      _id: string;
      runId: string;
      status: RunStatus;
      endedAt: number;
    }>([
      {
        $match: {
          orgId,
          sessionId: { $in: [...sessionIds] },
          actorId,
          status: { $in: [...TERMINAL_RUN_STATUSES] },
        },
      },
      { $sort: { sessionId: 1, updatedAt: -1, runId: -1 } },
      {
        $group: {
          _id: '$sessionId',
          runId: { $first: '$runId' },
          status: { $first: '$status' },
          endedAt: { $first: '$updatedAt' },
        },
      },
    ]);
    const bySession = new Map(rows.map((row) => [row._id, row]));
    return sessionIds.flatMap((sessionId) => {
      const row = bySession.get(sessionId);
      return row === undefined
        ? []
        : [
            {
              sessionId,
              runId: row.runId,
              status: row.status,
              endedAt: row.endedAt,
            },
          ];
    });
  }

  async openRuns(filter: OpenRunFilter, limit: number): Promise<OpenRun[]> {
    if (limit <= 0) return [];
    const query: Record<string, unknown> = {
      status: { $nin: [...TERMINAL_RUN_STATUSES] },
    };
    if (filter.orgId !== undefined) query['orgId'] = filter.orgId;
    if (filter.sessionId !== undefined) query['sessionId'] = filter.sessionId;
    if (filter.agentKey !== undefined) query['agentKey'] = filter.agentKey;
    if (filter.suspendedFor !== undefined) {
      query['status'] = 'paused';
      query['suspendedFor'] = filter.suspendedFor;
    }
    if (filter.engine !== undefined) {
      query['engineProfile.engine.kind'] = filter.engine.kind;
      query['engineProfile.engine.version'] = filter.engine.version;
    } else if (filter.engineOnly === true) {
      query['engineProfile'] = { $ne: null };
    }
    const documents = await AgentRunModel.find(query)
      .sort({ createdAt: 1 })
      .limit(limit)
      .read('primary')
      .lean();
    return documents.map((document) => {
      const raw = document as unknown as Record<string, unknown>;
      const engine = (raw['engineProfile'] as PinnedEngineProfile | null)
        ?.engine;
      return {
        run: runFromDocument(raw),
        engine:
          engine === undefined
            ? null
            : { kind: engine.kind, version: engine.version },
      };
    });
  }

  async strandedRuns(
    feature: WorkerFeature,
    waitedMs: number,
    limit: number,
  ): Promise<StrandedRun[]> {
    if (limit <= 0) return [];
    const documents = await AgentRunModel.find({
      status: 'queued',
      'requires.features': feature,
      'waitingForWorker.since': { $ne: null },
      $expr: {
        $lte: [
          '$waitingForWorker.since',
          { $subtract: [this.time.expr(), waitedMs] },
        ],
      },
    })
      .sort({ 'waitingForWorker.since': 1 })
      .limit(limit)
      .lean();
    return documents.map((document) => ({
      run: this.toRun(document),
      pinned: document.pinned != null,
    }));
  }

  async relaxRequirement(
    orgId: string,
    runId: string,
    revision: number,
    feature: WorkerFeature,
  ): Promise<boolean> {
    const written = await AgentRunModel.updateOne(
      {
        orgId,
        runId,
        revision,
        status: 'queued',
        pinned: null,
        'requires.features': feature,
      },
      [
        {
          $set: {
            'requires.features': {
              $filter: {
                input: '$requires.features',
                cond: { $ne: ['$$this', feature] },
              },
            },
            waitingForWorker: null,
            revision: { $add: ['$revision', 1] },
            updatedAt: this.time.expr(),
          },
        },
      ],
      { ...MAJORITY_WRITE, updatePipeline: true },
    );
    return written.modifiedCount === 1;
  }

  async markRunWaiting(
    orgId: string,
    runId: string,
    requires: ReturnType<typeof requirementsOf>,
  ): Promise<boolean> {
    const written = await AgentRunModel.updateOne(
      {
        orgId: eq(orgId),
        runId: eq(runId),
        status: 'queued',
        waitingForWorker: null,
      },
      [
        {
          $set: {
            waitingForWorker: {
              since: this.time.expr(),
              requires: { $literal: requires },
            },
          },
        },
      ],
      { ...MAJORITY_WRITE, updatePipeline: true },
    );
    return written.modifiedCount === 1;
  }

  /** Queued v2 runs, and for the inline claimer only its own. */
  private dueFor(role: ClaimRole): Record<string, unknown> {
    return {
      status: 'queued',
      harnessMajor: 2,
      ...(role === 'inline' ? { profile: 'inline' } : {}),
    };
  }

  /**
   * An inline run waits tClaimMs for its inline claimer before a
   * dispatched worker may take it; the inline claimer takes only its own.
   */
  private waitedFor(
    role: ClaimRole,
    tClaimMs: number,
  ): Record<string, unknown> {
    return role === 'inline'
      ? { $lte: [{ $ifNull: ['$notBefore', 0] }, this.time.expr()] }
      : {
          $lte: [
            {
              $add: [
                { $ifNull: ['$notBefore', 0] },
                { $cond: [{ $eq: ['$profile', 'inline'] }, tClaimMs, 0] },
              ],
            },
            this.time.expr(),
          ],
        };
  }

  async release(command: ReleaseAttempt): Promise<HarnessRun> {
    const released = await inMongoTransaction(async (session) => {
      const run = await AgentRunModel.findOneAndUpdate(
        {
          orgId: eq(command.orgId),
          runId: eq(command.runId),
          status: 'running',
          workerId: eq(command.workerId),
          attemptId: eq(command.attemptId),
          fence: eq(command.fence),
          $expr: this.isAfterNow('leaseExpiresAt'),
        },
        [
          {
            $set: {
              status: 'queued',
              workerId: null,
              attemptId: null,
              leaseExpiresAt: null,
              notBefore: this.time.expr(),
              revision: { $add: ['$revision', 1] },
              updatedAt: this.time.expr(),
              ...(unattemptedRelease(command.reason)
                ? {
                    attempts: {
                      $max: [
                        0,
                        { $subtract: [{ $ifNull: ['$attempts', 0] }, 1] },
                      ],
                    },
                  }
                : {}),
            },
          },
        ],
        { updatePipeline: true, returnDocument: 'after', session },
      ).lean();
      if (run === null) return null;
      await this.dispatches.enqueue(
        [
          {
            orgId: command.orgId,
            commandId: `release:${command.runId}:${String(command.fence)}`,
            runId: command.runId,
            kind: 'run_resume',
            createdAt: run.updatedAt,
          },
        ],
        mongoTx(session),
      );
      return this.toRun(run);
    });
    if (released === null) {
      await this.throwReleaseFailure(command);
      throw new Error('Attempt failure resolver returned unexpectedly');
    }
    return released;
  }

  now(_orgId: string): Promise<number> {
    return this.time.read();
  }

  async conversationDeleted(run: HarnessRun): Promise<boolean> {
    return !(await this.conversations.runSessionExists(run));
  }

  /** As the in-memory store answers (QA13-7): an attempt that still holds a cancelling run is not stale. */
  private async throwReleaseFailure(command: ReleaseAttempt): Promise<never> {
    const run = await AgentRunModel.findOne({
      orgId: command.orgId,
      runId: command.runId,
    }).lean();
    const now = await this.time.read();
    if (
      run !== null &&
      run.leaseExpiresAt !== null &&
      run.leaseExpiresAt > now &&
      run.absoluteDeadlineAt > now &&
      attemptOwnsRun(this.toRun(run), command)
    ) {
      throw new HarnessConflictError(
        'INVALID_TRANSITION',
        'Only a running attempt can be released',
      );
    }
    return this.throwAttemptFailure(command.orgId, command.runId);
  }

  async unclaimedRuns(ageMs: number, limit: number): Promise<HarnessRun[]> {
    if (limit <= 0) return [];
    const documents = await AgentRunModel.find({
      status: 'queued',
      attempts: 0,
      parent: null,
      trigger: null,
      waitingForWorker: null,
      $expr: {
        $lte: [
          { $max: ['$updatedAt', { $ifNull: ['$notBefore', 0] }] },
          { $subtract: [this.time.expr(), ageMs] },
        ],
      },
    })
      .sort({ updatedAt: 1 })
      .limit(limit)
      .lean();
    return documents.map((document) => this.toRun(document));
  }

  async promoteInline(tClaimMs: number, limit: number): Promise<number> {
    const due = await AgentRunModel.find({
      status: 'queued',
      profile: 'inline',
      $expr: {
        $lte: [
          { $add: [{ $ifNull: ['$notBefore', '$createdAt'] }, tClaimMs] },
          this.time.expr(),
        ],
      },
    })
      .sort({ notBefore: 1 })
      .limit(Math.max(limit, 1))
      .lean();
    let promoted = 0;
    for (const document of due) {
      const current = this.toRun(document);
      const won = await inMongoTransaction(async (session) => {
        const updated = await AgentRunModel.findOneAndUpdate(
          {
            orgId: current.orgId,
            runId: current.runId,
            revision: current.revision,
            status: 'queued',
            profile: 'inline',
          },
          [
            {
              $set: {
                profile: 'dispatched',
                revision: { $add: ['$revision', 1] },
                updatedAt: this.time.expr(),
              },
            },
          ],
          { updatePipeline: true, returnDocument: 'after', session },
        ).lean();
        if (updated === null) return false;
        await this.dispatches.enqueue(
          [
            {
              orgId: current.orgId,
              commandId: `promote:${current.runId}:${String(updated.revision)}`,
              runId: current.runId,
              kind: 'run_ready',
              createdAt: updated.updatedAt,
            },
          ],
          mongoTx(session),
        );
        return true;
      });
      if (won) promoted += 1;
    }
    return promoted;
  }

  async renew(command: RenewAttempt): Promise<HarnessRun> {
    this.validateLeaseDuration(command.leaseDurationMs);
    const run = await AgentRunModel.findOneAndUpdate(
      {
        orgId: eq(command.orgId),
        runId: eq(command.runId),
        status: { $in: [...WORKER_OWNED_RUN_STATUSES] },
        workerId: eq(command.workerId),
        attemptId: eq(command.attemptId),
        fence: eq(command.fence),
        $expr: {
          $and: [
            this.isAfterNow('leaseExpiresAt'),
            this.isAfterNow('absoluteDeadlineAt'),
          ],
        },
      },
      [
        {
          $set: {
            leaseExpiresAt: this.leaseUntil(command.leaseDurationMs),
            updatedAt: this.time.expr(),
          },
        },
      ],
      { updatePipeline: true, returnDocument: 'after', ...MAJORITY_WRITE },
    ).lean();
    if (run === null) {
      await this.throwAttemptFailure(command.orgId, command.runId);
      throw new Error('Attempt failure resolver returned unexpectedly');
    }
    return this.toRun(run);
  }

  async findReceipt(
    orgId: string,
    runId: string,
    commandId: string,
  ): Promise<StoredReceipt | null> {
    const prior = await AgentCommandReceiptModel.findOne({
      orgId: eq(orgId),
      runId: eq(runId),
      commandId: eq(commandId),
    })
      .read('primary')
      .readConcern('majority')
      .lean();
    return prior
      ? {
          payloadHash: prior.payloadHash,
          receipt: plain(prior.receipt as CommitReceipt),
        }
      : null;
  }

  async commit(
    command: CommitBatch,
    journal: readonly PreparedJournalAppend[],
    transcript?: RunTranscript,
  ): Promise<CommitReceipt> {
    const payloadHash = commandHash(command);
    try {
      return plain(
        await inMongoTransaction(
          async (session) => {
            const { run: currentDocument, prior: stored } =
              await this.runWithReceipt(command, session);
            const prior = this.replayOf(stored, payloadHash);
            if (prior) return { ...prior, replayed: true };
            if (!currentDocument) throw new HarnessNotFoundError();
            const current = this.toRun(currentDocument);
            this.validateOwnership(current, command);
            validateCommitBatch(current, command);
            // Read in this transaction: a resolution racing the suspend conflicts on the run document.
            assertSuspendable(current, command);
            const requested = requestedInteractions(command);
            const recorded = recordedRunFieldsOf(command);

            const transition = command.transition;
            const status = transition?.status ?? current.status;
            const terminal = TERMINAL_RUN_STATUSES.has(status);
            const releasesWorker = !WORKER_OWNED_RUN_STATUSES.has(status);
            const effects = transitionEffects(current, transition);
            const consumed = consumedInputs(command);
            // The turn's label goes with the run's ending in one write; without its transcript the transaction aborts.
            const label =
              childOf(current) === null ? transcript?.label : undefined;
            const holds = entersPersonWait(
              current,
              status,
              waitsForPerson(status) &&
                (await rootScheduled(currentDocument, session)),
            );
            const joining =
              status === 'paused' && effects.suspendedFor === 'join';
            // The driver directly (PERF-E3): every value here is already its stored type, and Mongoose's cast of a
            // pipeline update walks the run's whole schema on every commit.
            const updated = (await AgentRunModel.collection.findOneAndUpdate(
              {
                orgId: command.orgId,
                runId: command.runId,
                revision: current.revision,
                status: current.status,
                workerId: command.workerId,
                attemptId: command.attemptId,
                fence: command.fence,
                $expr: {
                  $and: [
                    this.isAfterNow('leaseExpiresAt'),
                    this.isAfterNow('absoluteDeadlineAt'),
                  ],
                },
              },
              [
                {
                  $set: {
                    status: { $literal: status },
                    ...(transition
                      ? { outcome: { $literal: transition.outcome ?? null } }
                      : {}),
                    ...(transition?.truncatedBy !== undefined
                      ? { truncatedBy: { $literal: transition.truncatedBy } }
                      : {}),
                    ...(transition?.checkpoint
                      ? { checkpoint: { $literal: transition.checkpoint } }
                      : {}),
                    ...(effects.attempts !== undefined
                      ? { attempts: effects.attempts }
                      : {}),
                    ...(effects.notBefore !== undefined
                      ? {
                          notBefore: {
                            $max: [this.time.expr(), effects.notBefore],
                          },
                        }
                      : {}),
                    ...(effects.checkpointRef !== undefined
                      ? { checkpointRef: { $literal: effects.checkpointRef } }
                      : {}),
                    ...(effects.profile !== undefined
                      ? { profile: { $literal: effects.profile } }
                      : {}),
                    ...(effects.suspendedFor !== undefined
                      ? { suspendedFor: { $literal: effects.suspendedFor } }
                      : {}),
                    ...(holds ? this.personWaitFields() : {}),
                    ...(releasesWorker
                      ? {
                          workerId: null,
                          attemptId: null,
                          leaseExpiresAt: null,
                        }
                      : {}),
                    ...(terminal ? { activeSessionKey: null } : {}),
                    ...(label !== undefined
                      ? { label: { $literal: label } }
                      : {}),
                    ...Object.fromEntries(
                      Object.entries(recorded ?? {}).map(([field, value]) => [
                        field,
                        { $literal: value },
                      ]),
                    ),
                    ...(requested.length > 0
                      ? {
                          openInteractions: {
                            $setUnion: [
                              { $ifNull: ['$openInteractions', []] },
                              { $literal: requested },
                            ],
                          },
                        }
                      : {}),
                    ...(consumed.length > 0
                      ? {
                          pendingInputs: {
                            $filter: {
                              input: { $ifNull: ['$pendingInputs', []] },
                              cond: { $not: [{ $in: ['$$this', consumed] }] },
                            },
                          },
                        }
                      : {}),
                    revision: { $add: ['$revision', 1] },
                    updatedAt: this.time.expr(),
                  },
                },
              ],
              { returnDocument: 'after', session },
            )) as CommittedRun | null;
            if (!updated) throw await this.ownershipFailure(current);
            const now = updated.updatedAt;
            if (joining) {
              const deadlines = await waitingChildDeadlines(current, session);
              if (deadlines.length > 0 || current.workLeftMs != null) {
                const fields = joinDeadline(
                  current,
                  deadlines,
                  updated.updatedAt,
                );
                await AgentRunModel.collection.updateOne(
                  { _id: updated._id },
                  { $set: fields },
                  { session },
                );
                Object.assign(updated, fields);
              }
            }
            if (holds || (joining && updated.workLeftMs != null)) {
              const held = this.toRun(updated);
              await holdAncestors(held, held.updatedAt, session);
            }

            if (command.workspaceHeadCAS) {
              await this.moveWorkspaceHead(command, now, session);
            }
            const appended = [];
            for (const append of journal) {
              appended.push(
                await this.journal.append(append, mongoTx(session)),
              );
            }
            const first = appended.at(0);
            const last = appended.at(-1);
            const range: JournalRange | null =
              first && last
                ? {
                    streamId: first.streamId,
                    firstSeq: first.firstSeq,
                    lastSeq: last.lastSeq,
                  }
                : null;

            const count = command.legacyEvents.length;
            let firstEventSeq: number | null = null;
            let lastEventSeq: number | null = null;
            if (count > 0) {
              const counter = await AgentEventCounterModel.findOneAndUpdate(
                { orgId: eq(command.orgId), sessionId: eq(current.sessionId) },
                { $inc: { nextEventSeq: count } },
                {
                  upsert: true,
                  returnDocument: 'after',
                  setDefaultsOnInsert: true,
                  session,
                },
              ).lean();
              lastEventSeq = counter.nextEventSeq;
              const allocatedFirstEventSeq = lastEventSeq - count + 1;
              firstEventSeq = allocatedFirstEventSeq;
              const events = command.legacyEvents.map((event, index) => ({
                orgId: command.orgId,
                sessionId: current.sessionId,
                runId: command.runId,
                eventSeq: allocatedFirstEventSeq + index,
                commitSeq: current.revision + 1,
                type: event.type,
                data: event.data,
                createdAt: now,
              }));
              await createInTransaction(session, AgentEventModel, events);
            }

            if (childOf(current) !== null) {
              if (transition !== undefined && terminal) {
                await joinParent(
                  current,
                  endingOf(transition),
                  now,
                  this.joinDeps,
                  session,
                );
              }
            } else if (transcript !== undefined) {
              if (
                !(await this.writeTranscript(
                  current,
                  transcript,
                  session,
                  true,
                )) &&
                !endsWithoutTranscript(transition?.status)
              ) {
                throw new ConversationDeletedError();
              }
            } else if (transition) {
              await this.projectTerminalTranscript(
                current,
                command,
                status,
                session,
                now,
              );
            }
            await this.dispatches.enqueue(
              command.outbox.map((item) => ({
                ...item,
                orgId: command.orgId,
                createdAt: now,
              })),
              mongoTx(session),
            );
            if (
              this.approverNotices !== null &&
              startsWaitingForPerson(current, status)
            ) {
              await this.approverNotices.enqueue(
                {
                  id: approverNoticeId(
                    command.orgId,
                    command.runId,
                    updated.revision,
                  ),
                  orgId: command.orgId,
                  runId: command.runId,
                  createdAt: now,
                },
                mongoTx(session),
              );
            }
            const result: CommitReceipt = {
              commandId: command.commandId,
              run: this.toRun(updated),
              firstEventSeq,
              lastEventSeq,
              journal: range,
            };
            await this.storeReceipt(command, payloadHash, result, now, session);
            return result;
          },
          { batchWrites: true },
        ),
      );
    } catch (error) {
      if (!isDuplicateKeyError(error)) throw error;
      return await this.resolveConcurrentCommand(
        command.orgId,
        command.runId,
        command.commandId,
        payloadHash,
      );
    }
  }

  async failRun(
    orgId: string,
    runId: string,
    errorType: string,
    journal: readonly PreparedJournalAppend[],
    holder?: string,
  ): Promise<boolean> {
    const document = await AgentRunModel.findOne({
      orgId: eq(orgId),
      runId: eq(runId),
    }).lean();
    if (!document) throw new HarnessNotFoundError();
    const current = this.toRun(document);
    if (TERMINAL_RUN_STATUSES.has(current.status)) return false;
    // `heldByAnotherAttempt` on DB time, in the compare-and-set: a renewal moves the lease without a revision.
    const notHeld = WORKER_OWNED_RUN_STATUSES.has(current.status)
      ? {
          $expr: {
            $or: [
              { $not: [this.isAfterNow('leaseExpiresAt')] },
              ...(holder !== undefined
                ? [{ $eq: ['$attemptId', holder] }]
                : []),
            ],
          },
        }
      : {};
    const expiry = await this.expire(
      current,
      notHeld,
      'failed',
      'run_failed',
      { errorType },
      journal,
    );
    return expiry !== 'lost';
  }

  private async moveWorkspaceHead(
    command: CommitBatch,
    now: number,
    sessionHandle: Tx | ClientSession,
  ): Promise<void> {
    const session = asMongoSession(sessionHandle);
    const head = command.workspaceHeadCAS;
    if (!head) return;
    const moved = await moveWorkspaceHead(
      { ...head, orgId: command.orgId, runId: command.runId },
      now,
      session,
    );
    if (!moved) {
      throw new HarnessConflictError(
        'WORKSPACE_CONFLICT',
        'Workspace HEAD moved since the expected revision',
      );
    }
  }

  async control(
    command: ControlRun,
    journal: readonly PreparedJournalAppend[] = [],
    transcript?: RunTranscript,
    fence?: CommitFence,
  ): Promise<CommitReceipt> {
    const payloadHash = commandHash(command);
    try {
      return plain(
        await inMongoTransaction(async (session) => {
          const prior = await this.priorReceipt(
            command.orgId,
            command.runId,
            command.commandId,
            payloadHash,
            session,
          );
          if (prior) return { ...prior, replayed: true };

          const currentDocument = await AgentRunModel.findOne({
            orgId: command.orgId,
            runId: command.runId,
          })
            .session(session)
            .lean();
          if (!currentDocument) throw new HarnessNotFoundError();
          const current = this.toRun(currentDocument);
          assertControlCurrent(current, command);
          await fence?.hold(session);

          let status: RunStatus;
          let eventType: string;
          let outboxKind: 'run_resume' | 'run_cancel' | null = null;
          const resolving =
            command.action === 'resolve'
              ? controlResolutions(command).map(
                  (resolution) => resolution.interactionId,
                )
              : null;
          if (resolving !== null) {
            if (resolving.length === 0) assertResolvable(current, '');
            for (const interactionId of resolving)
              assertResolvable(current, interactionId);
            if (RESUMABLE_RUN_STATUSES.has(current.status)) {
              if (current.absoluteDeadlineAt <= (await this.time.read())) {
                throw new HarnessConflictError(
                  'DEADLINE_EXCEEDED',
                  'Run deadline has elapsed',
                );
              }
              status = 'queued';
              eventType = 'run_resumed';
              outboxKind = 'run_resume';
            } else {
              status = current.status;
              eventType = 'interaction_resolved';
            }
          } else if (command.action === 'suspend') {
            assertRetirable(current);
            status = 'paused';
            eventType = 'run_suspended';
          } else if (command.action === 'resume') {
            if (command.cause !== undefined) assertRetired(current);
            if (!RESUMABLE_RUN_STATUSES.has(current.status)) {
              throw new HarnessConflictError(
                'INVALID_TRANSITION',
                'Run cannot be resumed',
              );
            }
            if (current.absoluteDeadlineAt <= (await this.time.read())) {
              throw new HarnessConflictError(
                'DEADLINE_EXCEEDED',
                'Run deadline has elapsed',
              );
            }
            status = 'queued';
            eventType = 'run_resumed';
            outboxKind = 'run_resume';
          } else {
            if (TERMINAL_RUN_STATUSES.has(current.status)) {
              throw new HarnessConflictError(
                'INVALID_TRANSITION',
                'Terminal run cannot be cancelled',
              );
            }
            if (WORKER_OWNED_RUN_STATUSES.has(current.status)) {
              status = 'cancelling';
              eventType = 'run_cancellation_requested';
              outboxKind = 'run_cancel';
            } else {
              status = 'cancelled';
              eventType = 'run_cancelled';
            }
          }

          // A run that tracks its open interactions resolves only ones still open, atomically.
          const tracked =
            resolving !== null && current.openInteractions !== undefined
              ? resolving
              : null;
          const keepsWorker = WORKER_OWNED_RUN_STATUSES.has(status);
          const terminal = TERMINAL_RUN_STATUSES.has(status);
          const needs =
            resolving !== null
              ? resolutionFeatures(controlResolutions(command))
              : [];
          const updated = await AgentRunModel.findOneAndUpdate(
            {
              orgId: eq(command.orgId),
              runId: eq(command.runId),
              // A commuting resolve guards the revision this transaction read: a concurrent write is a retried
              // write conflict, never a refusal.
              revision: eq(
                commutes(current, command)
                  ? current.revision
                  : command.expectedRevision,
              ),
              status: current.status,
              ...(tracked !== null
                ? { openInteractions: { $all: tracked } }
                : {}),
            },
            [
              {
                $set: {
                  status: { $literal: status },
                  ...(needs.length > 0
                    ? { requires: withFeatureExpr(...needs) }
                    : {}),
                  ...(tracked !== null
                    ? {
                        openInteractions: {
                          $filter: {
                            input: '$openInteractions',
                            cond: { $not: [{ $in: ['$$this', tracked] }] },
                          },
                        },
                      }
                    : {}),
                  ...(status === 'queued'
                    ? { notBefore: this.time.expr() }
                    : {}),
                  // A person answered or resumed it: they wait on it now, whether it re-queues or its worker still
                  // holds it in the approval hold (G-45).
                  // Written only when it rises: every other write names only the fields it changes (PERFB-11).
                  ...((status === 'queued' || resolving !== null) &&
                  afterApproval(priorityClassOf(current)) !==
                    priorityClassOf(current)
                    ? priorityFields(afterApproval(priorityClassOf(current)))
                    : {}),
                  ...(status === 'queued' && heldWorkLeft(current) !== null
                    ? {
                        // A person answered: the working deadline counts again from now (FIX-P1-F3).
                        absoluteDeadlineAt: {
                          $add: [this.time.expr(), '$workLeftMs'],
                        },
                        workLeftMs: null,
                      }
                    : {}),
                  ...(!keepsWorker
                    ? { workerId: null, attemptId: null, leaseExpiresAt: null }
                    : {}),
                  // G11: the first operator stop stays on the run, at DB time.
                  ...(command.action === 'cancel' &&
                  command.stop !== undefined &&
                  current.operatorStop === undefined
                    ? {
                        operatorStop: {
                          scope: { $literal: command.stop.scope },
                          reason: { $literal: command.stop.reason },
                          at: this.time.expr(),
                        },
                      }
                    : {}),
                  // ADR-OPS-01 D6: a retired run holds its working time until it is resumed or its hold ends.
                  ...(command.action === 'suspend'
                    ? {
                        suspendedFor: { $literal: RETIRED },
                        // The attempt was taken back, not lost: it counts toward no crash loop.
                        ...(current.attemptId !== null
                          ? {
                              attempts: {
                                $max: [
                                  0,
                                  {
                                    $subtract: [
                                      { $ifNull: ['$attempts', 0] },
                                      1,
                                    ],
                                  },
                                ],
                              },
                            }
                          : {}),
                        workLeftMs: {
                          $max: [
                            0,
                            {
                              $subtract: [
                                '$absoluteDeadlineAt',
                                this.time.expr(),
                              ],
                            },
                          ],
                        },
                        absoluteDeadlineAt: {
                          $max: [this.time.expr(), command.holdUntil ?? 0],
                        },
                      }
                    : {}),
                  ...(command.action === 'resume' && command.cause !== undefined
                    ? { suspendedFor: null }
                    : {}),
                  ...(terminal ? { activeSessionKey: null } : {}),
                  revision: { $add: ['$revision', 1] },
                  updatedAt: this.time.expr(),
                },
              },
            ],
            { updatePipeline: true, returnDocument: 'after', session },
          ).lean();
          if (!updated) {
            throw new HarnessConflictError(
              'REVISION_CONFLICT',
              'Run revision changed',
            );
          }
          const now = updated.updatedAt;
          if (status === 'queued' && heldWorkLeft(current) !== null) {
            await releaseAncestors(current, now, session);
          }
          if (command.action === 'suspend') {
            // ADR-RUN-01 D4: a retired run holds its working time, and the parents joined on it hold theirs.
            await holdAncestors(runFromDocument(updated), now, session);
          }
          for (const append of journal) {
            await this.journal.append(append, mongoTx(session));
          }
          if (terminal && childOf(current) !== null) {
            await joinParent(
              current,
              { status: 'cancelled', errorType: 'cancelled' },
              now,
              this.joinDeps,
              session,
            );
          }
          if (transcript !== undefined && terminal) {
            if (
              !(await this.writeTranscript(current, transcript, session)) &&
              !endsWithoutTranscript(status)
            ) {
              throw new ConversationDeletedError();
            }
          }

          const counter = await AgentEventCounterModel.findOneAndUpdate(
            { orgId: command.orgId, sessionId: current.sessionId },
            { $inc: { nextEventSeq: 1 } },
            {
              upsert: true,
              returnDocument: 'after',
              setDefaultsOnInsert: true,
              session,
            },
          ).lean();
          const eventSeq = counter.nextEventSeq;
          await AgentEventModel.create(
            [
              {
                orgId: command.orgId,
                sessionId: current.sessionId,
                runId: command.runId,
                eventSeq,
                commitSeq: current.revision + 1,
                type: eventType,
                data: {},
                createdAt: now,
              },
            ],
            { session },
          );
          if (outboxKind !== null) {
            await this.dispatches.enqueue(
              [
                {
                  orgId: command.orgId,
                  commandId: command.dispatchCommandId,
                  runId: command.runId,
                  kind: outboxKind,
                  createdAt: now,
                },
              ],
              mongoTx(session),
            );
          }

          const result: CommitReceipt = {
            commandId: command.commandId,
            run: this.toRun(updated),
            firstEventSeq: eventSeq,
            lastEventSeq: eventSeq,
          };
          await this.storeReceipt(command, payloadHash, result, now, session);
          return result;
        }),
      );
    } catch (error) {
      if (!isDuplicateKeyError(error)) throw error;
      return await this.resolveConcurrentCommand(
        command.orgId,
        command.runId,
        command.commandId,
        payloadHash,
      );
    }
  }

  async get(orgId: string, runId: string): Promise<HarnessRun | null> {
    const run = await AgentRunModel.findOne({
      orgId: eq(orgId),
      runId: eq(runId),
    }).lean();
    return run ? this.toRun(run) : null;
  }

  // Served by the session index {orgId, sessionId}: a session holds few runs.
  async completedRootChildren(
    orgId: string,
    sessionId: string,
    limit: number,
  ): Promise<HarnessRun[]> {
    const runs = await AgentRunModel.find({
      orgId,
      sessionId,
      'parent.depth': 1,
      status: 'completed',
    })
      .sort({ updatedAt: -1, runId: 1 })
      .limit(limit)
      .lean();
    return runs.map((run) => this.toRun(run));
  }

  // Served by the quota index {orgId, status, actorId}: admission bounds an org's active runs.
  async pendingApprovalRuns(
    orgId: string,
    filter: PendingApprovalFilter,
  ): Promise<HarnessRun[]> {
    const runs = await AgentRunModel.find({
      orgId,
      status: { $in: [...DECIDABLE_RUN_STATUSES] },
      'openInteractions.0': { $exists: true },
      ...(filter.actorId !== undefined ? { actorId: filter.actorId } : {}),
      ...(filter.agentKey !== undefined ? { agentKey: filter.agentKey } : {}),
    })
      .sort({ updatedAt: -1, runId: 1 })
      .limit(filter.limit)
      .lean();
    return runs.map((run) => this.toRun(run));
  }

  // Candidates by the quota index {orgId, status, actorId}; "still the latest" by the runs-by-session index.
  async unansweredQuestionRuns(
    orgId: string,
    filter: UnansweredQuestionFilter,
  ): Promise<HarnessRun[]> {
    const candidates = await AgentRunModel.find({
      orgId,
      status: 'completed',
      actorId: filter.actorId,
      outcome: 'needs_input',
      'parent.runId': { $exists: false },
      ...(filter.agentKey !== undefined ? { agentKey: filter.agentKey } : {}),
    })
      .sort({ updatedAt: -1, runId: 1 })
      .limit(filter.limit)
      .lean();
    if (candidates.length === 0) return [];
    const latest = await AgentRunModel.aggregate<{
      _id: string;
      createdAt: number;
    }>([
      {
        $match: {
          orgId,
          sessionId: {
            $in: [...new Set(candidates.map((run) => run.sessionId))],
          },
          'parent.runId': { $exists: false },
        },
      },
      { $group: { _id: '$sessionId', createdAt: { $max: '$createdAt' } } },
    ]);
    const newest = new Map(latest.map((row) => [row._id, row.createdAt]));
    return candidates
      .filter((run) => newest.get(run.sessionId) === run.createdAt)
      .map((run) => this.toRun(run));
  }

  async sessionRunParents(
    orgId: string,
    sessionId: string,
  ): Promise<RunParent[]> {
    const runs = await AgentRunModel.find({ orgId, sessionId })
      .select('runId parent')
      .lean();
    return runs.map((run) => ({
      runId: run.runId,
      parentRunId: run.parent?.runId ?? null,
    }));
  }

  async getActive(
    orgId: string,
    sessionId: string,
  ): Promise<HarnessRun | null> {
    const run = await AgentRunModel.findOne({
      orgId,
      activeSessionKey: activeSessionKey(orgId, sessionId),
    }).lean();
    return run ? this.toRun(run) : null;
  }

  async readEvents(
    orgId: string,
    sessionId: string,
    after: number,
    limit: number,
  ): Promise<HarnessEvent[]> {
    const events = await AgentEventModel.find({
      orgId,
      sessionId,
      eventSeq: { $gt: after },
    })
      .sort({ eventSeq: 1 })
      .limit(limit)
      .lean();
    return events.map((event) => ({
      orgId: event.orgId,
      sessionId: event.sessionId,
      runId: event.runId,
      eventSeq: event.eventSeq,
      commitSeq: event.commitSeq,
      type: event.type,
      data: plain(event.data),
      createdAt: event.createdAt,
    }));
  }

  async sweep(limit: number, transcripts?: SweptTranscripts): Promise<number> {
    const bound = Math.min(Math.max(limit, 1), 100);
    const leaseExpired = await AgentRunModel.find({
      status: { $in: [...WORKER_OWNED_RUN_STATUSES] },
      $expr: this.isNotAfterNow('leaseExpiresAt'),
    })
      .sort({ leaseExpiresAt: 1 })
      .limit(bound)
      .lean();
    const pastDeadline =
      leaseExpired.length < bound
        ? await AgentRunModel.find({
            status: { $in: [...IDLE_RUN_STATUSES] },
            $expr: this.isNotAfterNow('absoluteDeadlineAt'),
          })
            .sort({ absoluteDeadlineAt: 1 })
            .limit(bound - leaseExpired.length)
            .lean()
        : [];

    const now = leaseExpired.length > 0 ? await this.time.read() : 0;
    let swept = 0;
    for (const document of leaseExpired) {
      const current = this.toRun(document);
      const wasCancelling = current.status === 'cancelling';
      const deadlineElapsed = current.absoluteDeadlineAt <= now;
      const guard = { leaseExpiresAt: current.leaseExpiresAt };
      // C4.10 `recovery.autoResume` off: the lost worker's run ends; the person sends again.
      const lost =
        !wasCancelling && !deadlineElapsed && !resumesAfterLostWorker(current);
      const failing = !wasCancelling && (deadlineElapsed || lost);
      const failure: SweptFailure = lost ? WORKER_LOST : 'deadline_exceeded';
      // A lapsed cancel ends the run here: the calls its attempt left open end with it (EV-06). Its lease is
      // gone, so no worker commits between this read and the guarded transition.
      const open = wasCancelling
        ? ((await transcripts?.openCalls?.(current)) ?? [])
        : [];
      const next = wasCancelling ? 'cancelled' : 'queued';
      const won = failing
        ? await this.expireFailed(
            current,
            guard,
            lost ? { reason: WORKER_LOST } : {},
            transcripts,
            failure,
          )
        : (await this.expire(
            current,
            guard,
            next,
            wasCancelling ? 'run_cancelled' : 'run_recovery_queued',
            {},
            sweptJournal(current, next, open),
          )) !== 'lost';
      if (!won) continue;
      swept += 1;
      transcripts?.expired?.(
        current,
        failing ? 'failed' : wasCancelling ? 'cancelled' : 'queued',
        failing
          ? failure
          : wasCancelling
            ? 'cancel_lease_expired'
            : 'lease_expired',
      );
    }
    for (const document of pastDeadline) {
      const current = this.toRun(document);
      // A child's wait for a person moves its joined parent's deadline without a revision (WAITCHILD).
      const won = await this.expireFailed(
        current,
        { absoluteDeadlineAt: current.absoluteDeadlineAt },
        { reason: 'deadline_exceeded' },
        transcripts,
      );
      if (!won) continue;
      swept += 1;
      transcripts?.expired?.(current, 'failed', 'deadline_exceeded');
    }
    return swept;
  }

  /**
   * Fails a run at its deadline with its transcript in the same transaction.
   * A write that fails rolls that transaction back; the run is then failed
   * with its status only, so one bad conversation cannot hold its session
   * (or the rest of the page) past the deadline.
   */
  private async expireFailed(
    current: HarnessRun,
    guard: Record<string, unknown>,
    eventData: Record<string, unknown>,
    transcripts: SweptTranscripts | undefined,
    failure: SweptFailure = 'deadline_exceeded',
  ): Promise<boolean> {
    // No worker holds the run (its lease lapsed, or it waits): its open calls end with it (EV-06), under the
    // transition's guard.
    const open = (await transcripts?.openCalls?.(current)) ?? [];
    const expire = (withTranscript?: RunTranscript): Promise<Expiry> =>
      this.expire(
        current,
        guard,
        'failed',
        'run_failed',
        eventData,
        sweptJournal(current, 'failed', open, failure),
        withTranscript,
      );
    const appending = sweptJournalEvents(current, 'failed', open, failure);
    const transcript =
      transcripts !== undefined && appending.length > 0
        ? await transcripts.project(current, appending)
        : undefined;
    if (transcripts === undefined || transcript === undefined) {
      return (await expire()) !== 'lost';
    }
    let outcome: SweptTranscriptOutcome;
    let cause: unknown;
    let expiry: Expiry;
    try {
      expiry = await expire(transcript);
      outcome = expiry === 'session_missing' ? 'session_missing' : 'written';
    } catch (error) {
      if (!(error instanceof TranscriptProjectionError)) throw error;
      cause = error.cause;
      outcome = 'write_failed';
      expiry = await expire();
    }
    if (expiry === 'lost') return false;
    transcripts.settled(current, transcript, outcome, cause);
    return true;
  }

  /** One CAS transition out of an expired state; `lost` when another writer won. */
  private async expire(
    current: HarnessRun,
    guard: Record<string, unknown>,
    nextStatus: RunStatus,
    eventType: string,
    eventData: Record<string, unknown>,
    journal: readonly PreparedJournalAppend[] = sweptJournal(
      current,
      nextStatus,
    ),
    transcript?: RunTranscript,
  ): Promise<Expiry> {
    return inMongoTransaction(async (session): Promise<Expiry> => {
      const updated = await AgentRunModel.findOneAndUpdate(
        {
          orgId: current.orgId,
          runId: current.runId,
          revision: current.revision,
          status: current.status,
          ...guard,
        },
        [
          {
            $set: {
              status: { $literal: nextStatus },
              workerId: null,
              attemptId: null,
              leaseExpiresAt: null,
              ...(nextStatus === 'queued'
                ? { notBefore: this.time.expr() }
                : {}),
              ...(TERMINAL_RUN_STATUSES.has(nextStatus)
                ? { activeSessionKey: null }
                : {}),
              revision: { $add: ['$revision', 1] },
              updatedAt: this.time.expr(),
            },
          },
        ],
        { updatePipeline: true, returnDocument: 'after', session },
      ).lean();
      if (!updated) return 'lost';
      const now = updated.updatedAt;
      for (const append of journal) {
        await this.journal.append(append, mongoTx(session));
      }

      const counter = await AgentEventCounterModel.findOneAndUpdate(
        { orgId: current.orgId, sessionId: current.sessionId },
        { $inc: { nextEventSeq: 1 } },
        {
          upsert: true,
          returnDocument: 'after',
          setDefaultsOnInsert: true,
          session,
        },
      ).lean();
      await AgentEventModel.create(
        [
          {
            orgId: current.orgId,
            sessionId: current.sessionId,
            runId: current.runId,
            eventSeq: counter.nextEventSeq,
            commitSeq: current.revision + 1,
            type: eventType,
            data: eventData,
            createdAt: now,
          },
        ],
        { session },
      );
      if (!TERMINAL_RUN_STATUSES.has(nextStatus)) {
        await this.dispatches.enqueue(
          [
            {
              orgId: current.orgId,
              commandId: `recover:${current.runId}:${String(current.fence)}:${String(current.revision + 1)}`,
              runId: current.runId,
              kind: 'run_resume',
              createdAt: now,
            },
          ],
          mongoTx(session),
        );
      } else if (childOf(current) !== null) {
        await joinParent(
          current,
          sweptEnding(nextStatus, eventData),
          now,
          this.joinDeps,
          session,
        );
      } else if (
        transcript !== undefined &&
        (await this.writeTranscript(current, transcript, session))
      ) {
        return 'expired';
      } else {
        await this.conversations.setConversationStatus(
          current,
          nextStatus === 'failed' ? 'Failed' : 'Complete',
          now,
          mongoTx(session),
        );
        if (transcript !== undefined) return 'session_missing';
      }
      return 'expired';
    });
  }

  /** Everything but time; lease and deadline are checked on DB time by the CAS. */
  private validateOwnership(run: HarnessRun, command: CommitBatch): void {
    if (
      !WORKER_OWNED_RUN_STATUSES.has(run.status) ||
      run.workerId !== command.workerId ||
      run.attemptId !== command.attemptId ||
      run.fence !== command.fence
    ) {
      throw new HarnessConflictError(
        'STALE_ATTEMPT',
        'Worker no longer owns this run',
      );
    }
    if (
      command.transition?.expectedRevision !== undefined &&
      run.revision !== command.transition.expectedRevision
    ) {
      throw new HarnessConflictError(
        'REVISION_CONFLICT',
        'Run revision changed',
      );
    }
  }

  private async projectTerminalTranscript(
    run: HarnessRun,
    command: CommitBatch,
    status: RunStatus,
    sessionHandle: Tx | ClientSession,
    now: number,
  ): Promise<void> {
    const session = asMongoSession(sessionHandle);
    if (status !== 'completed') {
      if (status === 'failed') {
        await this.conversations.setConversationStatus(
          run,
          'Failed',
          now,
          mongoTx(session),
        );
      }
      return;
    }
    const completion = [...command.legacyEvents]
      .reverse()
      .find(
        (event) => event.type === 'run_completed' || event.type === 'complete',
      )?.data;
    if (
      completion === undefined ||
      completion === null ||
      typeof completion !== 'object' ||
      typeof (completion as Record<string, unknown>).answer !== 'string'
    ) {
      await this.conversations.setConversationStatus(
        run,
        'Complete',
        now,
        mongoTx(session),
      );
      return;
    }
    const saved = await this.conversations.saveCompletedConversation(
      run,
      completion as Record<string, unknown>,
      mongoTx(session),
    );
    if (!saved) throw new HarnessNotFoundError();
  }

  /** `labelled`: the caller's own write of the run in this transaction carries the label. */
  private async writeTranscript(
    run: HarnessRun,
    transcript: RunTranscript,
    sessionHandle: Tx | ClientSession,
    labelled = false,
  ): Promise<boolean> {
    const session = asMongoSession(sessionHandle);
    let saved: boolean;
    try {
      saved = await this.conversations.saveRunTranscript(
        run,
        transcript,
        mongoTx(session),
      );
    } catch (error) {
      // A write conflict (another participant's admission wrote the
      // conversation, SS2) is retried with the whole transaction, not failed.
      if (isTransientTransactionError(error)) throw error;
      throw new TranscriptProjectionError(error);
    }
    if (saved && !labelled && transcript.label !== undefined) {
      await AgentRunModel.updateOne(
        { orgId: run.orgId, runId: run.runId },
        { $set: { label: transcript.label } },
        { session },
      );
    }
    return saved;
  }

  async storeFollowups(
    orgId: string,
    runId: string,
    followups: readonly Followup[],
  ): Promise<FollowupsWrite | null> {
    const stored = followups.map(({ text }) => ({ text }));
    const written = await AgentRunModel.updateOne(
      { orgId, runId, status: 'completed', followups: { $exists: false } },
      { $set: { followups: stored } },
      MAJORITY_WRITE,
    );
    if (written.modifiedCount === 1) {
      return { outcome: 'stored', followups: stored };
    }
    const document = await AgentRunModel.findOne({
      orgId: eq(orgId),
      runId: eq(runId),
    })
      .select('status followups')
      .read('primary')
      .lean();
    if (document === null) return null;
    const { status, followups: kept } = document as {
      status: RunStatus;
      followups?: Followup[];
    };
    if (status !== 'completed') return { outcome: 'not_completed' };
    return { outcome: 'kept', followups: kept ?? [] };
  }

  async readEnding(orgId: string, runId: string): Promise<RunEnding | null> {
    const document = await AgentRunModel.findOne({
      orgId: eq(orgId),
      runId: eq(runId),
    })
      .read('primary')
      .lean();
    if (document === null) return null;
    const run = this.toRun(document);
    if (!TERMINAL_RUN_STATUSES.has(run.status)) return null;
    return {
      run,
      label: parseTurnLabel((document as { label?: unknown }).label),
    };
  }

  async historyLabels(run: HarnessRun): Promise<Array<TurnLabel | null>> {
    const stored = await AgentRunModel.findOne({
      orgId: run.orgId,
      runId: run.runId,
    })
      .select('audience createdAt')
      .lean();
    if (stored === null) return [null];
    const audience = (stored as { audience?: RunAudience }).audience;
    const createdAt = (stored as { createdAt: number }).createdAt;
    const seen = seenRuns(audience);
    const latest = await AgentRunModel.findOne({
      orgId: run.orgId,
      sessionId: run.sessionId,
      parent: null,
      runId:
        seen === null
          ? idOperator({ $ne: run.runId })
          : { $in: seen.own.filter((id) => id !== run.runId) },
      createdAt: { $lte: createdAt },
    })
      .sort({ createdAt: -1, _id: -1 })
      .select('runId label')
      .lean();
    const visible =
      seen === null || seen.visible.length === 0
        ? []
        : await AgentRunModel.find({
            orgId: run.orgId,
            sessionId: run.sessionId,
            runId: { $in: seen.visible },
          })
            .select('runId label')
            .lean();
    const labelOf = (document: unknown): TurnLabel | null =>
      parseTurnLabel((document as { label?: unknown }).label);
    return [
      ...(latest === null ? [] : [labelOf(latest)]),
      ...(seen?.visible ?? []).map((runId) => {
        const found = visible.find((document) => document.runId === runId);
        return found === undefined ? null : labelOf(found);
      }),
    ];
  }

  /** Why a CAS that passed every non-time check failed: its deadline or its lease. */
  private async ownershipFailure(run: HarnessRun): Promise<Error> {
    if (run.absoluteDeadlineAt <= (await this.time.read())) {
      return new HarnessConflictError(
        'DEADLINE_EXCEEDED',
        'Run deadline has elapsed',
      );
    }
    return new HarnessConflictError(
      'STALE_ATTEMPT',
      'Worker no longer owns this run',
    );
  }

  private async priorReceipt(
    orgId: string,
    runId: string,
    commandId: string,
    payloadHash: string,
    sessionHandle: Tx | ClientSession,
  ): Promise<CommitReceipt | null> {
    const session = asMongoSession(sessionHandle);
    const prior = await AgentCommandReceiptModel.findOne({
      orgId: eq(orgId),
      runId: eq(runId),
      commandId: eq(commandId),
    })
      .session(session)
      .lean();
    return this.replayOf(prior, payloadHash);
  }

  /** A stored receipt as the replay it answers; a command id used for another payload is refused. */
  private replayOf(
    prior: { payloadHash: string; receipt: unknown } | null | undefined,
    payloadHash: string,
  ): CommitReceipt | null {
    if (!prior) return null;
    if (prior.payloadHash !== payloadHash) {
      throw new HarnessConflictError(
        'IDEMPOTENCY_CONFLICT',
        'The command ID was already used with a different transition',
      );
    }
    return prior.receipt as CommitReceipt;
  }

  /**
   * A commit's two reads in its transaction, the run and its command's receipt, in one command from one snapshot
   * (WP88-PF05-5: one store round trip less on every commit's path). A receipt is found only with its run: a
   * receipt whose run is gone was answered by the replay check before the transaction.
   */
  private async runWithReceipt(
    command: { orgId: string; runId: string; commandId: string },
    sessionHandle: Tx | ClientSession,
  ): Promise<{
    run: Record<string, unknown> | null;
    prior: { payloadHash: string; receipt: unknown } | null;
  }> {
    const session = asMongoSession(sessionHandle);
    const [found] = await AgentRunModel.aggregate<
      Record<string, unknown> & {
        [PRIOR_RECEIPT]: { payloadHash: string; receipt: unknown }[];
      }
    >([
      { $match: { orgId: command.orgId, runId: command.runId } },
      { $limit: 1 },
      // What a find leaves out (a path declared `select: false`), so the run is the one `findOne` answered.
      { $project: { [REGION_PATH]: 0 } },
      {
        $lookup: {
          from: AgentCommandReceiptModel.collection.collectionName,
          pipeline: [
            {
              $match: {
                orgId: command.orgId,
                runId: command.runId,
                commandId: command.commandId,
              },
            },
            { $limit: 1 },
          ],
          as: PRIOR_RECEIPT,
        },
      },
    ]).session(session);
    if (found === undefined) return { run: null, prior: null };
    const { [PRIOR_RECEIPT]: receipts, ...run } = found;
    return { run, prior: receipts[0] ?? null };
  }

  private async storeReceipt(
    command: { orgId: string; runId: string; commandId: string },
    payloadHash: string,
    receipt: CommitReceipt,
    now: number,
    sessionHandle: Tx | ClientSession,
  ): Promise<void> {
    const session = asMongoSession(sessionHandle);
    const stored = [
      {
        orgId: command.orgId,
        runId: command.runId,
        commandId: command.commandId,
        payloadHash,
        receipt,
        createdAt: now,
      },
    ];
    await createInTransaction(session, AgentCommandReceiptModel, stored);
  }

  private async throwClaimFailure(
    orgId: string,
    runId: string,
    worker?: ClaimingWorker,
  ): Promise<never> {
    const run = await AgentRunModel.findOne({
      orgId: eq(orgId),
      runId: eq(runId),
    }).lean();
    if (!run) throw new HarnessNotFoundError();
    if (run.absoluteDeadlineAt <= (await this.time.read())) {
      throw new HarnessConflictError(
        'DEADLINE_EXCEEDED',
        'Run deadline has elapsed',
      );
    }
    if (
      worker !== undefined &&
      missingFor(this.toRun(run), worker).length > 0
    ) {
      throw workerIncompatible();
    }
    throw new HarnessConflictError('RUN_NOT_CLAIMABLE', 'Run is not claimable');
  }

  private async throwAttemptFailure(
    orgId: string,
    runId: string,
  ): Promise<never> {
    const run = await AgentRunModel.findOne({
      orgId: eq(orgId),
      runId: eq(runId),
    }).lean();
    if (!run) throw new HarnessNotFoundError();
    if (run.absoluteDeadlineAt <= (await this.time.read())) {
      throw new HarnessConflictError(
        'DEADLINE_EXCEEDED',
        'Run deadline has elapsed',
      );
    }
    throw new HarnessConflictError(
      'STALE_ATTEMPT',
      'Worker no longer owns this run',
    );
  }

  private validateLeaseDuration(durationMs: number): void {
    if (!Number.isSafeInteger(durationMs) || durationMs <= 0) {
      throw new HarnessConflictError(
        'DEADLINE_EXCEEDED',
        'Lease duration must be positive',
      );
    }
  }

  private async resolveConcurrentCommand(
    orgId: string,
    runId: string,
    commandId: string,
    payloadHash: string,
  ): Promise<CommitReceipt> {
    const receipt = await AgentCommandReceiptModel.findOne({
      orgId: eq(orgId),
      runId: eq(runId),
      commandId: eq(commandId),
    }).lean();
    if (receipt) {
      if (receipt.payloadHash !== payloadHash) {
        throw new HarnessConflictError(
          'IDEMPOTENCY_CONFLICT',
          'The command ID was already used with a different transition',
        );
      }
      // Lost the race to the identical command: a replay, so the service neither ends the turn nor projects again.
      return { ...plain(receipt.receipt as CommitReceipt), replayed: true };
    }
    throw new HarnessConflictError(
      'IDEMPOTENCY_CONFLICT',
      'A durable command identifier is already in use',
    );
  }

  private toRun(document: object): HarnessRun {
    return runFromDocument(document);
  }
}

/** The ids of every run of one conversation, by the `{orgId, sessionId}` index. */
export async function runIdsOfSession(
  orgId: string,
  sessionId: string,
): Promise<string[]> {
  const runs = await AgentRunModel.find(
    { orgId, sessionId },
    { _id: 0, runId: 1 },
  ).lean<Array<{ runId: string }>>();
  return runs.map((run) => run.runId);
}

/** One list state as a run filter (ADR-UI-01 D1), the Mongo reading of `runListState`. */
function mongoStatePredicate(state: RunListState): Record<string, unknown> {
  const statuses = { $in: [...STATE_STATUSES[state]] };
  if (state === 'needs_you') {
    return {
      status: statuses,
      $or: [
        { status: 'waiting_approval' },
        { 'openInteractions.0': { $exists: true } },
      ],
    };
  }
  if (state === 'working') {
    return { status: statuses, 'openInteractions.0': { $exists: false } };
  }
  return { status: statuses };
}
