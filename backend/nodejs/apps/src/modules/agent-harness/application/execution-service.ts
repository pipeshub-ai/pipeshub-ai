import { workerInput } from '../domain/worker-input';
import { runTreeOf, skippedConnectsIn } from '../domain/answered-run';
import { CONVERSATION_GRANTS_OFF_MESSAGE } from '../domain/run-policy-fields';
import type { RuleGrantTarget } from '../ports/tool-rules';
import {
  ClaimFloor,
  DEFAULT_CLAIM_FLOOR_EVERY,
  type ClaimOrder,
} from '../domain/priority-class';
import { dependencyHealth } from '../ops/dependency-health';
import type { CommitFence } from '../ports/commit-fence';
import { performance } from 'perf_hooks';
import { noRunWakeup, RunWakeup } from '../ports/run-wakeup';
import { canonicalSha256 } from '../contracts/canonical-json';
import { modelRouteId } from '../domain/gateway-token';
import {
  blockedRequestsOf,
  EffectivePolicy,
  effectivePolicyOf,
  HELD_REQUEST_EVENTS,
  heldRequestsOf,
  ResolvedRunView,
} from '../domain/effective-policy';
import {
  HarnessConflictError,
  ConversationDeletedError,
  HarnessNotFoundError,
  HarnessUnavailableError,
  JournalPayloadMissingError,
  JournalRejectedError,
  PayloadStoreUnavailableError,
  RunNotCompletedError,
  TranscriptProjectionError,
} from '../domain/errors';
import {
  type ConversationGrant,
  CONVERSATION_GRANT_ACTOR_ONLY_MESSAGE,
  type DeclinableAsk,
  declinableAsk,
  grantOffered,
  offeredGrant,
  type RunGrantState,
  runGrantState,
  sessionDecisions,
  coveringDecline,
  widestGrants,
} from '../domain/conversation-grants';
import {
  ClaimedRun,
  ClaimNextRun,
  ClaimPinning,
  ClaimRun,
  attemptMayReadRun,
  attemptOwnsRun,
  childOf,
  CommitBatch,
  commitBatchFromTransition,
  CommitReceipt,
  CommitTransition,
  controlJournalEvents,
  openToolCalls,
  controlResolutions,
  ControlRun,
  HarnessEvent,
  HarnessRun,
  InteractionDecision,
  journalStreamId,
  ReleaseAttempt,
  RenewAttempt,
  runSpecPinnedEvent,
  StoreClock,
  systemStoreClock,
  T_CLAIM_MS,
  INTERACTIVE_CLAIM_DEADLINE_MS,
  NOT_STARTED,
  TERMINAL_RUN_STATUSES,
  transitionJournalEvents,
  transitionOutbox,
  validateCommitBatch,
  WORKER_OWNED_RUN_STATUSES,
} from '../domain/execution';
import { BudgetAmount, BudgetUsage, ZERO_USAGE } from '../domain/budget-ledger';
import type { BudgetLedgerStore } from '../ports/budget-ledger';
import { Followup } from '../domain/followups';
import {
  JOURNAL_PAYLOAD_MISSING,
  JournalEvent,
  JournalEventInput,
  JournalPosition,
  PreparedJournalAppend,
} from '../domain/journal';
import { RunTranscript } from '../ports/conversation-writer';
import { runTranscript } from '../projection/transcript';
import {
  joinLabels,
  MAX_LABEL_CHILD_DEPTH,
  ownWorkLabel,
  spawnedChildren,
  TurnLabel,
} from '../domain/turn-labels';
import { leftInputs } from '../domain/steering';
import {
  assertControlCurrent,
  requestedInteractions,
  resolutionJournalEvents,
  runAsksKey,
} from '../domain/interactions';
import { isWorkingJob } from '../domain/run-lists';
import {
  ClaimSubject,
  ExecutionStore,
  StoredReceipt,
  SweepReason,
  SweptTranscriptOutcome,
  SweptTranscripts,
} from '../ports/execution-store';
import { CapabilitySource } from '../ports/capability-probe';
import { ChildRunStore } from '../ports/child-runs';
import {
  CAPABILITY_SETTLE_WINDOW_MS,
  CapabilitySnapshot,
  codeSandboxProof,
  pinnableCapabilities,
} from '../domain/capabilities';
import { settledCapabilities } from './capability-service';
import {
  PinnedRunSpec,
  pinRunSpec,
  RELEASE_PIN_FEATURE,
  principalRefFor,
  readPinnedSpec,
  RUN_SPEC_UPCASTERS,
  RunSpecResolver,
  RunSpecUpcaster,
  SpecSubject,
} from '../domain/run-spec';
import { JournalStore } from '../ports/journal-store';
import { forEnumeratedOrg } from '../../../libs/context/orgContext';
import {
  ChatPathMetrics,
  ChatTurnOutcome,
  noChatPathMetrics,
} from '../ports/chat-path-metrics';
import {
  AGENT_CODE_FEATURE,
  ClaimingWorker,
  GUARDED_WORKER_FEATURES,
  NO_COMPATIBLE_WORKER,
  missingFor,
  requirementsOf,
  RunRequirements,
  WORKER_INCOMPATIBLE,
  workerIncompatible,
  type WorkerFeature,
} from '../domain/worker-compat';
import {
  WORKER_FEATURE_WINDOW_MS,
  WorkerFeatureAvailability,
} from './worker-feature-availability';
import { JournalService } from './journal-service';
import type { RunHistoryContent } from './run-history-content';
import { RunInputSeal, UNSEALED_RUN_INPUT } from './run-input-seal';
import { RunJournalReader } from './run-journal-reader';
import { engineImageDigests, EngineImageSource } from './engine-image-pin';
import type { HistoryTurnEntry } from '../domain/session-audience';
import {
  elapsedMs,
  harnessLogMeta,
  HarnessLogger,
  runLogFields,
  silentHarnessLogger,
} from './harness-log';
import { ENGINE_OFF_ERROR, type EngineEligibility } from './engine-eligibility';

/** The job a claim's per-run phase is bound as (the run's org, from the cross-org pick or the attempt's claims). */
export const CLAIM_JOB = 'agent-harness.claim';
/** The job a sweep's per-run work is bound as: the org of the run its cross-org read found. */
export const SWEEP_JOB = 'agent-harness.sweep';
export const MAX_SWEEP_LIMIT = 100;
export const MAX_JOURNAL_PAGE = 500;
/** Candidates one claim-next tries; each lost race or refused run costs a claim. */
export const CLAIM_NEXT_CANDIDATES = 5;
/** Runs an empty claim-next marks as waiting for another worker, per poll (RU-2e). */
export const WAITING_SCAN_LIMIT = 20;
/** How long a run waits in the queue after the payload store failed it. */
export const PAYLOAD_STORE_BACKOFF_MS = 30_000;

/** Journal page size for the run tree's skipped connects (WP-64). */
const SKIP_SCAN_PAGE = 500;

export type JournalReader = Pick<
  JournalStore<unknown>,
  'readStream' | 'readRun'
>;

/** The claimed worker's read of its journal (events_after). */
export interface ReadRunJournal {
  orgId: string;
  runId: string;
  workerId: string;
  attemptId: string;
  fence: number;
  afterSeq: number;
  limit: number;
  /** `stream`: the run's whole stream (session state); `run`: this run's events. */
  scope: 'stream' | 'run';
}

export const commitPayloadHash = (batch: CommitBatch): string =>
  canonicalSha256(batch);

const SPEC_UNRESOLVABLE = 'SPEC_UNRESOLVABLE';
/** Claim-next skips these candidates; anything else is the caller's problem. */
const SKIPPABLE_CLAIM_CODES = new Set([
  'RUN_NOT_CLAIMABLE',
  'DEADLINE_EXCEEDED',
  SPEC_UNRESOLVABLE,
  'SPEC_NOT_PINNED',
  WORKER_INCOMPATIBLE,
]);

/** A refusal's reason code for logs: the conflict code, never the message. */
const refusalCode = (error: unknown): string =>
  error instanceof HarnessConflictError ||
  error instanceof ConversationDeletedError
    ? error.code
    : error instanceof HarnessNotFoundError
      ? 'RUN_NOT_FOUND'
      : error instanceof PayloadStoreUnavailableError
        ? 'PAYLOAD_STORE_UNAVAILABLE'
        : error instanceof Error
          ? error.name
          : 'unknown';

const isSkippableClaimError = (error: unknown): boolean =>
  error instanceof HarnessNotFoundError ||
  error instanceof PayloadStoreUnavailableError ||
  // The claim failed the run for it: the queue moves on.
  error instanceof JournalPayloadMissingError ||
  (error instanceof HarnessConflictError &&
    SKIPPABLE_CLAIM_CODES.has(error.code));

/** Told of each run the sweeper expired, as it was, and why (E1: its sandboxes are reaped). */
export type SweptRunObserver = (run: HarnessRun, reason: SweepReason) => void;

/** Told once a run's ending committed, by whichever path ended it (SS2: the next run is admitted). */
export type RunEndedListener = (run: HarnessRun) => void;

/** A usage-bearing journal event (`model.response`, `usage.recorded`) as the auditor folds it (EV-10). */
export interface AuditUsageRecord {
  type: string;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  /** Absent: counted as its reporter counts (the fold's legacy rule needs provider and transport). */
  inputIncludesCached?: boolean;
  provider?: string;
  transport?: string;
}

/** P9: a run's grant and its tool intents, as the grant auditor reads them. */
export interface RunAuditEvidence {
  orgId: string;
  runId: string;
  pinned: boolean;
  toolIds: string[];
  routeIds: string[];
  intents: Array<{
    invocationId: string;
    toolId: string;
    attemptId: string | null;
  }>;
  /** The run ended: its usage may be reconciled (C9.01). */
  ended: boolean;
  /** The journal's usage, one record per usage-bearing event. */
  usage: AuditUsageRecord[];
  /**
   * The run's ledger account (OWD-8: the usage authority); null when it has none. `used` is null on an
   * account no settler ever told a usage (one opened before the ledger kept usage): nothing to reconcile.
   */
  ledger: { used: BudgetUsage | null; reserved: BudgetAmount } | null;
}

const count = (value: unknown): number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
    ? value
    : 0;

/**
 * Seconds from when `run` could first be claimed (admission, or a later `notBefore`) to the claim that just took it,
 * all in DB time: the claim set its lease to end `leaseDurationMs` after it, on the store's clock, as admission set
 * `createdAt` (a Node replica's own clock is never compared with either).
 */
function firstClaimWaitS(
  run: HarnessRun,
  leaseDurationMs: number,
): number | undefined {
  if (run.leaseExpiresAt === null) return undefined;
  const claimedAt = run.leaseExpiresAt - leaseDurationMs;
  return (
    Math.max(0, claimedAt - Math.max(run.createdAt, run.notBefore ?? 0)) / 1000
  );
}

/** `model.response`'s `usage` or `usage.recorded`'s `tokens`; ids and counts only. */
function auditUsage(event: JournalEvent): AuditUsageRecord | null {
  const data = event.data ?? {};
  const raw =
    event.type === 'model.response'
      ? data['usage']
      : event.type === 'usage.recorded'
        ? data['tokens']
        : undefined;
  if (raw === null || typeof raw !== 'object') return null;
  const usage = raw as Record<string, unknown>;
  return {
    type: event.type,
    inputTokens: count(usage['inputTokens']),
    outputTokens: count(usage['outputTokens']),
    cacheReadTokens: count(usage['cacheReadTokens']),
    cacheWriteTokens: count(usage['cacheWriteTokens']),
    ...(typeof usage['inputIncludesCached'] === 'boolean'
      ? { inputIncludesCached: usage['inputIncludesCached'] }
      : {}),
    ...(typeof data['provider'] === 'string'
      ? { provider: data['provider'] }
      : {}),
    ...(typeof data['transport'] === 'string'
      ? { transport: data['transport'] }
      : {}),
  };
}

/**
 * Told once a run's ending committed with its turn label (P2.5a), by a
 * worker's terminal commit, a cancel or a sweep (P7.3: the run's proposed
 * memories settle with it). Must not throw.
 */
export type RunLabelledListener = (run: HarnessRun, label: TurnLabel) => void;

/**
 * Told of a run that ended with no turn label and never will have one (H2):
 * Node failed or cancelled it without a transcript. Must not throw.
 */
export type RunUnlabelledListener = (run: HarnessRun) => void;

/** Told once a commit moved a run into `waiting_approval` (an edition may notify whoever else may decide). Must not throw. */
export type RunWaitingListener = (run: HarnessRun) => void;

/**
 * Told of a run a command moved to another status that is not an end (`RunEndedListener` tells those): a worker commit
 * that suspends it for a person, an answer that sends it back to the queue (UIB5-FU-1). A claim is not told.
 */
export type RunStatusChangedListener = (run: HarnessRun) => void;

/**
 * Told once a command changed what a run asks of its person (`runAsksKey`): an ask raised or settled, a run that
 * began or stopped waiting, a run that ended on a question or ended with asks still open (ADR-INBOX-02). Must not throw.
 */
export type RunAsksChangedListener = (run: HarnessRun) => void;

/**
 * Told once a command moved a job into or out of the person's working jobs (`isWorkingJob`, CJ14-FU-1): a claim
 * that started it, a release that put it back in the queue, its end. Replays are not told. Must not throw.
 */
export type RunWorkingChangedListener = (run: HarnessRun) => void;

/**
 * Told of the asks a worker commit raised that offer "allow for this conversation" (WP115-FU-2), after the commit and
 * before its receipt is answered. A failure leaves the asks to the person.
 */
export type GrantableAsksListener = (
  run: HarnessRun,
  interactionIds: readonly string[],
  commandId: string,
) => Promise<unknown>;

/** Reads a run's history through its reference (QA22-F2). */
export type RunHistoryReader = Pick<RunHistoryContent, 'read'>;

export interface ExecutionServiceOptions {
  store: ExecutionStore;
  journal: JournalReader;
  journals: JournalService;
  /** Absent: no admission here keeps a history by reference. */
  history?: RunHistoryReader | null;
  /** Opens the query a run's document keeps sealed (LCFIX); absent: queries stored in plaintext. */
  inputs?: RunInputSeal | null;
  /** The Node's reader of run journals, shared with its streams; its own when absent. */
  runJournal?: RunJournalReader;
  capabilities?: CapabilitySource | null;
  resolveSpec?: RunSpecResolver;
  upcasters?: Readonly<Record<number, RunSpecUpcaster>>;
  logger?: HarnessLogger;
  /** For Node-authored backoffs; the store never applies a notBefore before DB now. */
  clock?: StoreClock;
  /** Child runs (P6.1): cancelled with their parent, swept when it ended without them. */
  children?: ChildRunStore | null;
  /** The broker's engine image digests an engine run's claim pins (C2.20); absent: none pinned. */
  engineImages?: EngineImageSource | null;
  /** The budget ledger the grant auditor reconciles a run's journal usage with (C9.01); absent: none. */
  budgets?: Pick<BudgetLedgerStore, 'account'> | null;
  /** Every n-th claim-next serves the oldest due run of any class (G-45); 0: strict priority. */
  claimFloorEvery?: number;
  /**
   * WP-111: an engine run's claim starts an engine session, so its engine must still be on for the org and its
   * actor's groups; one an admin turned off fails `engine_off` instead. Absent: not checked.
   */
  engineEligibility?: Pick<EngineEligibility, 'check'> | null;
}

export class ExecutionService {
  private readonly endedListeners: RunEndedListener[] = [];
  private readonly labelledListeners: RunLabelledListener[] = [];
  private readonly unlabelledListeners: RunUnlabelledListener[] = [];
  private readonly waitingListeners: RunWaitingListener[] = [];
  private readonly movedListeners: RunStatusChangedListener[] = [];
  private readonly grantableAskListeners: GrantableAsksListener[] = [];
  private readonly asksListeners: RunAsksChangedListener[] = [];
  private readonly workingListeners: RunWorkingChangedListener[] = [];
  /** Runs told labelled whose ending was not yet seen: every path labels a run before it ends it. */
  private readonly labelledRuns = new Set<string>();
  private workerFeatures: WorkerFeatureAvailability | null = null;
  private guardedFeatures: readonly WorkerFeature[] = GUARDED_WORKER_FEATURES;
  private wakeup: RunWakeup = noRunWakeup;
  private metrics: ChatPathMetrics = noChatPathMetrics;

  private readonly store: ExecutionStore;
  private readonly journal: JournalReader;
  private readonly journals: JournalService;
  private readonly history: RunHistoryReader | null;
  private readonly inputs: RunInputSeal;
  private readonly runJournal: RunJournalReader;
  private readonly capabilities: CapabilitySource | null;
  private readonly resolveSpec: RunSpecResolver;
  private readonly upcasters: Readonly<Record<number, RunSpecUpcaster>>;
  private readonly logger: HarnessLogger;
  private readonly clock: StoreClock;
  private readonly children: ChildRunStore | null;
  private readonly engineImages: EngineImageSource | null;
  private readonly budgets: Pick<BudgetLedgerStore, 'account'> | null;
  private readonly floor: ClaimFloor;
  private readonly engineEligibility: Pick<EngineEligibility, 'check'> | null;

  constructor(options: ExecutionServiceOptions) {
    this.store = options.store;
    this.journal = options.journal;
    this.journals = options.journals;
    this.history = options.history ?? null;
    this.inputs = options.inputs ?? UNSEALED_RUN_INPUT;
    this.runJournal =
      options.runJournal ??
      new RunJournalReader({
        journal: options.journal,
        journals: options.journals,
        logger: options.logger,
      });
    this.capabilities = options.capabilities ?? null;
    this.resolveSpec = options.resolveSpec ?? pinRunSpec;
    this.upcasters = options.upcasters ?? RUN_SPEC_UPCASTERS;
    this.logger = options.logger ?? silentHarnessLogger;
    this.clock = options.clock ?? systemStoreClock;
    this.children = options.children ?? null;
    this.engineImages = options.engineImages ?? null;
    this.engineEligibility = options.engineEligibility ?? null;
    this.budgets = options.budgets ?? null;
    this.floor = new ClaimFloor(
      options.claimFloorEvery ?? DEFAULT_CLAIM_FLOOR_EVERY,
    );
  }

  /** Production metrics of the durable path (DUR3): turns, claims, replayed commands. */
  measureWith(metrics: ChatPathMetrics): void {
    this.metrics = metrics;
  }

  /** `listener` is told of every run that ends from now on; it must not throw. */
  onRunEnded(listener: RunEndedListener): void {
    this.endedListeners.push(listener);
  }

  /** `listener` is told of every run that ends with a turn label from now on; it must not throw. */
  onRunLabelled(listener: RunLabelledListener): void {
    this.labelledListeners.push(listener);
  }

  /** `listener` is told of every run that ends without a turn label from now on; it must not throw. */
  onRunUnlabelled(listener: RunUnlabelledListener): void {
    this.unlabelledListeners.push(listener);
  }

  /** `listener` is told of every run a worker commit suspends for a person from now on; it must not throw. */
  onRunWaiting(listener: RunWaitingListener): void {
    this.waitingListeners.push(listener);
  }

  /** `listener` is told of every run a command moves to another status short of an end from now on; it must not throw. */
  onRunStatusChanged(listener: RunStatusChangedListener): void {
    this.movedListeners.push(listener);
  }

  /** `listener` is told of every run whose asks a command changes from now on (`RunAsksChangedListener`). */
  onRunAsksChanged(listener: RunAsksChangedListener): void {
    this.asksListeners.push(listener);
  }

  /** `listener` is told of every job a command moves into or out of the working jobs (`RunWorkingChangedListener`). */
  onRunWorkingChanged(listener: RunWorkingChangedListener): void {
    this.workingListeners.push(listener);
  }

  /** `listener` is told of the grantable asks each worker commit raises from now on (`GrantableAsksListener`). */
  onGrantableAsks(listener: GrantableAsksListener): void {
    this.grantableAskListeners.push(listener);
  }

  /**
   * The asks a worker commit raised whose reason, journaled in the same commit, offers a conversation grant or names
   * the call a person's decline could cover (ADR-GRANT-01 D9).
   */
  private async grantableAsks(
    receipt: CommitReceipt,
    batch: CommitBatch,
  ): Promise<void> {
    if (
      this.grantableAskListeners.length === 0 ||
      receipt.replayed === true ||
      TERMINAL_RUN_STATUSES.has(receipt.run.status)
    ) {
      return;
    }
    const offered = new Set<string>();
    for (const event of batch.events) {
      const id = event.data['interactionId'];
      if (
        event.type === 'interaction.explained' &&
        typeof id === 'string' &&
        (grantOffered(event.data) || declinableAsk(event.data) !== null)
      ) {
        offered.add(id);
      }
    }
    const asked = requestedInteractions(batch).filter((id) => offered.has(id));
    if (asked.length === 0) return;
    for (const listener of this.grantableAskListeners) {
      try {
        await listener(receipt.run, asked, batch.commandId);
      } catch (error) {
        this.logger.warn(
          'interaction.grant_held_failed',
          harnessLogMeta('interaction.grant_held_failed', {
            ...runLogFields(receipt.run),
            asks: asked.length,
            errorType: error instanceof Error ? error.name : typeof error,
          }),
        );
      }
    }
  }

  /** A replayed receipt was told by the command that wrote it; a run already waiting was told when it began to. */
  private waitedBy(before: HarnessRun, receipt: CommitReceipt): void {
    if (
      receipt.replayed === true ||
      before.status === 'waiting_approval' ||
      receipt.run.status !== 'waiting_approval'
    ) {
      return;
    }
    for (const listener of this.waitingListeners) listener(receipt.run);
  }

  /** A replayed receipt was told by the command that wrote it. */
  private asksMovedBy(before: HarnessRun, receipt: CommitReceipt): void {
    if (receipt.replayed !== true) this.asksMoved(before, receipt.run);
  }

  private asksMoved(before: HarnessRun, after: HarnessRun): void {
    this.workingMoved(before, after);
    if (runAsksKey(before) === runAsksKey(after)) return;
    tell(this.asksListeners, after);
  }

  private workingMoved(before: HarnessRun, after: HarnessRun): void {
    if (isWorkingJob(before) !== isWorkingJob(after)) {
      tell(this.workingListeners, after);
    }
  }

  /** A replayed receipt was told by the command that wrote it. */
  private movedBy(before: HarnessRun, receipt: CommitReceipt): void {
    if (
      receipt.replayed === true ||
      before.status === receipt.run.status ||
      TERMINAL_RUN_STATUSES.has(receipt.run.status)
    ) {
      return;
    }
    for (const listener of this.movedListeners) listener(receipt.run);
  }

  private labelled(
    run: HarnessRun,
    transcript: RunTranscript | undefined,
  ): void {
    if (transcript?.label === undefined) return;
    this.labelledRuns.add(run.runId);
    for (const listener of this.labelledListeners) {
      listener(run, transcript.label);
    }
  }

  /**
   * A replayed receipt lost a race with the identical command that wrote it,
   * maybe on another instance: that command told the run's ending, with its
   * label, and telling it again would settle the run as unlabelled.
   */
  private endedBy(before: HarnessRun, receipt: CommitReceipt): void {
    if (receipt.replayed !== true) this.ended(before, receipt.run);
  }

  private ended(before: Pick<HarnessRun, 'status'>, after: HarnessRun): void {
    const wasLabelled = this.labelledRuns.delete(after.runId);
    if (
      TERMINAL_RUN_STATUSES.has(before.status) ||
      !TERMINAL_RUN_STATUSES.has(after.status)
    ) {
      return;
    }
    if (after.parent === undefined) {
      this.metrics.turnEnded(
        'durable',
        after.agentKey !== undefined ? 'agent' : 'assistant',
        turnOutcome(after),
        (this.clock.now() - after.createdAt) / 1000,
      );
    }
    for (const listener of this.endedListeners) listener(after);
    if (!wasLabelled) {
      for (const listener of this.unlabelledListeners) listener(after);
    }
  }

  /**
   * A v2 run's spec is resolved once, for the first claim, from what
   * admission fixed; the claim transaction pins it only if the run is still
   * unpinned, so a re-claim or retry reads the pin and never resolves again
   * (P-1). The pin is returned as stored, once this build has read it.
   */
  async claim(command: ClaimRun): Promise<ClaimedRun> {
    if (command.worker !== undefined) {
      await this.workerFeatures?.observe(command.worker.features);
    }
    try {
      return await this.claimRun(command);
    } catch (error) {
      this.logger.info(
        'run.claim_refused',
        harnessLogMeta('run.claim_refused', {
          orgId: command.orgId,
          runId: command.runId,
          attemptId: command.attemptId,
          workerId: command.workerId,
          reason: refusalCode(error),
        }),
      );
      throw error;
    }
  }

  private async claimRun(
    command: ClaimRun,
    via: 'hint' | 'poll' = 'hint',
  ): Promise<ClaimedRun> {
    const started = performance.now();
    const read = await this.store.readClaimSubject(
      command.orgId,
      command.runId,
    );
    if (read === null) throw new HarnessNotFoundError();
    // The spec pins the run's filters (knowledge selection): resolved from the opened input (GAPS-1).
    const subject = { ...read, run: await this.inputs.open(read.run) };
    const worker = command.worker;
    if (worker !== undefined) {
      const missing = missingFor(subject.run, worker);
      if (missing.includes('region')) {
        // Placement (OWD-12): another region's worker never takes it, nor marks it as waiting for one.
        throw new HarnessConflictError(
          'RUN_PLACEMENT_REFUSED',
          "The run's data lives in another region than this worker's",
        );
      }
      if (missing.length > 0) {
        const requires = requirementsOf(subject.run);
        if (
          await this.store.markRunWaiting(
            command.orgId,
            command.runId,
            requires,
          )
        ) {
          this.logFiltered(
            subject.run,
            requires,
            command,
            worker,
            via,
            missing,
          );
        }
        throw workerIncompatible();
      }
    }
    const waited = subject.run.waitingForWorker ?? null;
    const pinning =
      subject.run.harnessMajor === 2 && subject.pinned === null
        ? await this.pinOrFail(subject)
        : undefined;
    const pinMs = elapsedMs(started);
    await this.refuseEngineOff(subject, pinning);
    // Before the claim: a store that cannot answer leaves the run unclaimed.
    const early = await this.admittedHistory(subject);
    const taken = await this.store.claim(command, pinning);
    const claimed = withHistory(
      { ...taken, run: await this.inputs.open(taken.run) },
      early === 'not_visible'
        ? await this.historyOfClaimed(subject, taken.run)
        : early,
    );
    this.metrics.claimed(
      claimed.run.claimedFrom ?? 'queued',
      subject.run.fence === 0
        ? firstClaimWaitS(claimed.run, command.leaseDurationMs)
        : undefined,
    );
    const ms = elapsedMs(started);
    this.logger.info(
      'run.claimed',
      harnessLogMeta('run.claimed', {
        ...runLogFields(claimed.run),
        workerId: command.workerId,
        attempts: claimed.run.attempts ?? null,
        from: claimed.run.claimedFrom ?? null,
        harnessMajor: claimed.run.harnessMajor,
        pinned: pinning !== undefined ? 'first_claim' : 'existing',
        seq: claimed.journal.lastSeq[journalStreamId(claimed.run)] ?? null,
        workerFoldVersion: worker?.foldVersion ?? null,
        waitedForWorkerMs:
          waited !== null ? Math.max(0, this.clock.now() - waited.since) : null,
        ms,
        pinMs,
      }),
    );
    if (pinning !== undefined) {
      this.logger.info(
        'run.spec_pinned',
        harnessLogMeta('run.spec_pinned', {
          ...runLogFields(claimed.run),
          specDigest: pinning.pinned.specDigest,
          capabilitiesDigest: pinning.pinned.capabilitiesDigest,
          tools: pinning.pinned.spec.grants.tools.length,
          storage:
            pinning.specEvent.payloadRef !== undefined
              ? 'claim_check'
              : 'inline',
        }),
      );
    }
    // This Node must read the pin, but the worker gets it as stored: the
    // claim filter matched the worker on the stored version
    // (`servedSchemaVersion`), so an upcast view could be one it cannot read.
    if (claimed.pinned !== undefined) {
      readPinnedSpec(claimed.pinned.spec, this.upcasters);
    }
    // A replayed claim read the run already taken, so it moves nothing.
    this.workingMoved(subject.run, claimed.run);
    return claimed;
  }

  /**
   * WP-111: a run pinned to an engine an admin has since turned off starts no new engine session: its first claim
   * and a claim after a lost attempt both fail it `engine_off`, and the parent's loop reads a failed child. A
   * live attempt is not cut short here: the store fails no run another attempt holds on a live lease, so a claim
   * for one is refused as any claim of a held run is, and an operator's stop (ADR-OPS-01) is what ends work
   * already running.
   */
  private async refuseEngineOff(
    subject: ClaimSubject,
    pinning: ClaimPinning | undefined,
  ): Promise<void> {
    if (this.engineEligibility === null) return;
    const stored = pinning?.pinned ?? subject.pinned ?? null;
    if (stored === null) return;
    const executor = readPinnedSpec(stored.spec, this.upcasters).executor;
    if (executor?.kind !== 'engine') return;
    const engine = {
      kind: executor.engine.kind,
      version: executor.engine.version,
    };
    const off = await this.engineEligibility.check(
      subject.run.orgId,
      subject.run.actorId,
      engine,
    );
    if (off !== 'engine_off' && off !== 'engine_group_required') return;
    const failed = await this.failRun(subject.run, ENGINE_OFF_ERROR);
    this.logger.info(
      'run.engine_off',
      harnessLogMeta('run.engine_off', {
        ...runLogFields(subject.run),
        engine: engine.kind,
        version: engine.version,
        reason: off,
        outcome: failed ? 'failed' : 'left_to_holder',
      }),
    );
    throw new HarnessConflictError(
      'RUN_NOT_CLAIMABLE',
      'The coding engine this run is pinned to is turned off',
    );
  }

  /**
   * The history the run was admitted with, when its document names it
   * instead of holding it (QA22-F2). `not_visible`: a part is not there. The
   * run document was read as committed and the parts as majority-committed,
   * so a run claimed in the instant after its admission can be ahead of its
   * history; the claim's own transaction settles which it is.
   */
  private async admittedHistory(
    subject: ClaimSubject,
  ): Promise<HistoryTurnEntry[] | 'not_visible' | null> {
    const ref = subject.history ?? null;
    // An ended run is refused by the claim itself; its history may be erased by now.
    if (ref === null || TERMINAL_RUN_STATUSES.has(subject.run.status)) {
      return null;
    }
    if (this.history === null) {
      throw new HarnessUnavailableError(
        'The run names a stored history and no history reader is configured',
      );
    }
    try {
      return await this.history.read(subject.run, ref);
    } catch (error) {
      if (error instanceof JournalPayloadMissingError) return 'not_visible';
      throw error;
    }
  }

  /**
   * The history once the claim committed: the admission is majority-committed
   * by then, so a part still gone never comes back and the run fails, as it
   * does for a journal payload that is gone. A store that cannot answer gives
   * the attempt back.
   */
  private async historyOfClaimed(
    subject: ClaimSubject,
    claimed: HarnessRun,
  ): Promise<HistoryTurnEntry[] | null> {
    const ref = subject.history ?? null;
    if (ref === null || this.history === null) return null;
    try {
      return await this.history.read(claimed, ref);
    } catch (error) {
      if (error instanceof JournalPayloadMissingError) {
        this.logger.warn(
          'run.history_unreadable',
          harnessLogMeta('run.history_unreadable', {
            ...runLogFields(claimed),
            reason: error.reason,
            turns: ref.turns,
            size: ref.size,
            outcome: 'failed',
          }),
        );
        await this.failRun(
          claimed,
          JOURNAL_PAYLOAD_MISSING,
          claimed.attemptId ?? undefined,
        );
      } else if (claimed.attemptId !== null && claimed.workerId !== null) {
        await this.release({
          orgId: claimed.orgId,
          runId: claimed.runId,
          workerId: claimed.workerId,
          attemptId: claimed.attemptId,
          fence: claimed.fence,
          reason: 'history_unavailable',
        });
      }
      throw error;
    }
  }

  /**
   * The Mongo claim is the queue (D20): the claimable run of the highest
   * priority class for this role, oldest first within it, claimed through
   * `claim`; every n-th claim takes the oldest of any class, so no class
   * starves (G-45). A full worker preempting asks only above the class it will
   * preempt, never on the floor. A candidate another claimer won, or one whose
   * spec cannot be resolved (now failed), moves on to the next.
   */
  async claimNext(command: ClaimNextRun): Promise<ClaimedRun | null> {
    if (command.worker !== undefined) {
      await this.workerFeatures?.observe(command.worker.features);
    }
    const order: ClaimOrder =
      command.aboveClass !== undefined
        ? { above: command.aboveClass }
        : { floor: this.floor.next() };
    const candidates = await this.store.nextClaimable(
      command.role,
      T_CLAIM_MS,
      CLAIM_NEXT_CANDIDATES,
      command.worker,
      order,
    );
    if (
      candidates.length === 0 &&
      command.worker !== undefined &&
      command.aboveClass === undefined
    ) {
      await this.markWaiting(command, command.worker);
    }
    for (const candidate of candidates) {
      try {
        // The per-run phase runs as the candidate's org, as the cross-org pick found it.
        return await forEnumeratedOrg(candidate.orgId, CLAIM_JOB, () =>
          this.claimRun(
            {
              orgId: candidate.orgId,
              runId: candidate.runId,
              workerId: command.workerId,
              attemptId: command.attemptId,
              leaseDurationMs: command.leaseDurationMs,
              ...(command.worker !== undefined
                ? { worker: command.worker }
                : {}),
            },
            'poll',
          ),
        );
      } catch (error) {
        if (!isSkippableClaimError(error)) throw error;
        this.logger.debug(
          'run.claim_next_skipped',
          harnessLogMeta('run.claim_next_skipped', {
            ...candidate,
            role: command.role,
            reason:
              error instanceof HarnessConflictError
                ? error.code
                : error instanceof PayloadStoreUnavailableError
                  ? 'payload_store_unavailable'
                  : error instanceof JournalPayloadMissingError
                    ? error.errorType
                    : 'not_found',
          }),
        );
      }
    }
    return null;
  }

  /**
   * Nothing this worker can run is due: the due runs it cannot run are
   * marked and logged once each, so a run no live worker can take is
   * visible while it waits (it is never failed for it).
   */
  private async markWaiting(
    command: ClaimNextRun,
    worker: ClaimingWorker,
  ): Promise<void> {
    const marked = await this.store.markWaiting(
      command.role,
      T_CLAIM_MS,
      WAITING_SCAN_LIMIT,
      worker,
    );
    for (const run of marked) {
      this.logFiltered(
        run,
        run.requires,
        command,
        worker,
        'poll',
        missingFor({ harnessMajor: 2, requires: run.requires }, worker),
      );
    }
  }

  private logFiltered(
    run: { orgId: string; runId: string; sessionId: string },
    requires: RunRequirements,
    command: { workerId: string },
    worker: ClaimingWorker,
    via: 'hint' | 'poll',
    missing: string[],
  ): void {
    this.logger.info(
      'claim.filtered',
      harnessLogMeta('claim.filtered', {
        orgId: run.orgId,
        sessionId: run.sessionId,
        runId: run.runId,
        workerId: command.workerId,
        reason: 'schema',
        via,
        missing,
        workerFoldVersion: worker.foldVersion,
        runFoldVersion: requires.foldVersion,
        workerSchemaVersions: worker.supportedSchemaVersions.map(String),
        runSchemaVersion: requires.schemaVersion ?? null,
        workerFeatures: worker.features,
        runFeatures: requires.features,
        outcome: 'waiting',
      }),
    );
  }

  /** Gives the attempt back to the queue with no journaled transition. */
  async release(command: ReleaseAttempt): Promise<HarnessRun> {
    const run = await this.store.release(command);
    // The attempt was running, so a run released back to the queue (Up next) is no longer at work.
    if (run.parent !== undefined && !isWorkingJob(run)) {
      tell(this.workingListeners, run);
    }
    const meta = harnessLogMeta('run.released', {
      ...runLogFields(command),
      sessionId: run.sessionId,
      reason: command.reason,
    });
    // A token that could not be issued is a degraded authority, not a worker choice.
    if (command.reason === 'attempt_token_unavailable') {
      this.logger.warn('run.released', meta);
    } else {
      this.logger.info('run.released', meta);
    }
    return run;
  }

  /**
   * A spec that cannot be resolved never will be (its inputs are immutable
   * admission data), so the run fails terminally instead of staying queued.
   */
  private async pinOrFail(subject: SpecSubject): Promise<ClaimPinning> {
    try {
      return await this.preparePin(subject);
    } catch (error) {
      if (
        error instanceof HarnessConflictError &&
        error.code === SPEC_UNRESOLVABLE
      ) {
        this.logger.warn(
          'run.spec_unresolvable',
          harnessLogMeta('run.spec_unresolvable', {
            ...runLogFields(subject.run),
            reason: SPEC_UNRESOLVABLE,
            detail: error.message,
            outcome: 'failed',
          }),
        );
        await this.failRun(subject.run, 'spec_unresolvable');
      }
      throw error;
    }
  }

  private async preparePin(subject: SpecSubject): Promise<ClaimPinning> {
    const pinsImage =
      subject.engineProfile !== undefined &&
      subject.engineProfile !== null &&
      (subject.run.requires?.features ?? []).includes(RELEASE_PIN_FEATURE);
    const engineImages = pinsImage
      ? await engineImageDigests(
          this.engineImages,
          this.logger,
          runLogFields(subject.run),
        )
      : null;
    const pinned = this.resolveSpec(
      engineImages !== null ? { ...subject, engineImages } : subject,
      pinnableCapabilities(await this.claimCapabilities(subject.run)),
    );
    const prepared = await this.journals.prepare({
      ...this.position(subject.run),
      writer: 'node',
      events: [runSpecPinnedEvent(pinned)],
    });
    const specEvent = prepared.events[0];
    if (specEvent === undefined)
      throw new Error('run.spec_pinned was not prepared');
    return { pinned, specEvent };
  }

  /**
   * What a first claim pins (ADR-CAP-01 D3). A run admitted while its sandbox
   * was unknown requires `agent_code`, and its pin is decided here: after one
   * bounded probe round, a sandbox still unknown leaves the run queued (a
   * skippable refusal, the next poll asks again) until the settle window since
   * its admission ends; past it the broker is down, not starting, and the run
   * pins what is known, logged.
   */
  private async claimCapabilities(
    run: HarnessRun,
  ): Promise<CapabilitySnapshot | null> {
    const source = this.capabilities;
    if (source === null) return null;
    if (!(run.requires?.features ?? []).includes(AGENT_CODE_FEATURE)) {
      return source.current();
    }
    const snapshot = await settledCapabilities(source, 'sandbox.isolation');
    if (codeSandboxProof(snapshot) !== 'unknown') return snapshot;
    const waitedMs = Math.max(0, this.clock.now() - run.createdAt);
    const fields = {
      ...runLogFields(run),
      reason: 'capabilities_unsettled',
      waitedMs,
    };
    if (waitedMs < CAPABILITY_SETTLE_WINDOW_MS) {
      this.logger.info(
        'run.claim_deferred',
        harnessLogMeta('run.claim_deferred', fields),
      );
      // Not claimable now: the worker moves on and the next poll asks again.
      throw new HarnessConflictError(
        'RUN_NOT_CLAIMABLE',
        'The sandbox broker has not answered yet; the run waits for it',
      );
    }
    this.logger.warn(
      'run.sandbox_unsettled',
      harnessLogMeta('run.sandbox_unsettled', {
        ...fields,
        outcome: 'pinned_without_sandbox',
      }),
    );
    return snapshot;
  }

  async renew(command: RenewAttempt): Promise<HarnessRun> {
    let run: HarnessRun;
    try {
      run = await this.store.renew(command);
    } catch (error) {
      this.logger.warn(
        'run.renew_refused',
        harnessLogMeta('run.renew_refused', {
          ...runLogFields(command),
          workerId: command.workerId,
          reason: refusalCode(error),
        }),
      );
      throw error;
    }
    this.logger.debug(
      'run.renewed',
      harnessLogMeta('run.renewed', {
        ...runLogFields(run),
        leaseMs: command.leaseDurationMs,
      }),
    );
    // A busy worker polls for no claim: its renewals show what it runs.
    await this.workerFeatures?.observe(run.attemptFeatures ?? []);
    return run;
  }

  /**
   * Accepts the durable-v1 flat shape too, for workers not yet upgraded.
   * `read`: the run as the caller's authorization just read it, in place of
   * this commit's own read outside its transaction; the store's transaction
   * reads and fences the run either way. `prior`: the replay check's read
   * (`priorCommit`), sent by the caller alongside its authorization's.
   */
  async commit(
    command: CommitTransition | CommitBatch,
    read?: HarnessRun | null,
    prior?: Promise<StoredReceipt | null>,
  ): Promise<CommitReceipt> {
    try {
      return await this.commitCommand(command, read ?? null, prior);
    } catch (error) {
      const refusal = await this.namedCause(command, error);
      this.logCommitRefusal(command, refusal);
      throw refusal;
    }
  }

  /**
   * A worker's terminal commit refused because erasure stopped its run first answers the cause, as the commit
   * whose transcript found no session does (QA21-F4): either way the worker learns the conversation was deleted.
   */
  private async namedCause(
    command: CommitTransition | CommitBatch,
    error: unknown,
  ): Promise<unknown> {
    if (
      !(error instanceof HarnessConflictError) ||
      error.code !== 'INVALID_TRANSITION' ||
      !('transition' in command) ||
      command.transition === undefined
    ) {
      return error;
    }
    const run = await this.store.get(command.orgId, command.runId);
    if (
      run === null ||
      (run.status !== 'cancelling' && run.status !== 'cancelled')
    ) {
      return error;
    }
    return (await this.store.conversationDeleted(run))
      ? new ConversationDeletedError()
      : error;
  }

  /**
   * A commit's replay check: the receipt its command id already has. It reads nothing an attempt check decides, so
   * the internal route sends it with that check instead of after it (WP88-PF05-3: one store round trip less on
   * every commit's path, PF-05's two per gateway call among them).
   */
  priorCommit(
    orgId: string,
    runId: string,
    commandId: string,
  ): Promise<StoredReceipt | null> {
    return this.store.findReceipt(orgId, runId, commandId);
  }

  /** Refusals are the worker's only view of why a commit did not land. */
  private logCommitRefusal(
    command: CommitTransition | CommitBatch,
    error: unknown,
  ): void {
    const fields = {
      orgId: command.orgId,
      runId: command.runId,
      attemptId: command.attemptId,
      fence: command.fence,
      commandId: command.commandId,
    };
    if (error instanceof JournalRejectedError) {
      const events = 'events' in command ? command.events : [];
      const event = events[error.index] as { type?: unknown } | undefined;
      this.logger.warn(
        'journal.rejected',
        harnessLogMeta('journal.rejected', {
          ...fields,
          reason: error.code,
          index: error.index,
          type:
            typeof event?.type === 'string' ? event.type.slice(0, 128) : null,
        }),
      );
    } else if (
      error instanceof HarnessConflictError ||
      error instanceof HarnessNotFoundError
    ) {
      const meta = harnessLogMeta('run.commit_refused', {
        ...fields,
        reason: refusalCode(error),
      });
      // Expected: the worker takes the queued input in a follow-up turn.
      if (
        error instanceof HarnessConflictError &&
        error.code === 'INPUT_PENDING'
      ) {
        this.logger.info('run.commit_refused', meta);
      } else {
        this.logger.warn('run.commit_refused', meta);
      }
    }
  }

  private async commitCommand(
    command: CommitTransition | CommitBatch,
    read: HarnessRun | null,
    priorRead?: Promise<StoredReceipt | null>,
  ): Promise<CommitReceipt> {
    const started = performance.now();
    const normalized =
      'status' in command ? commitBatchFromTransition(command) : command;
    const batch = await this.withTransitionOutbox(normalized);
    const prior = await (priorRead ??
      this.store.findReceipt(batch.orgId, batch.runId, batch.commandId));
    if (prior !== null) {
      if (prior.payloadHash !== commitPayloadHash(batch)) {
        throw new HarnessConflictError(
          'IDEMPOTENCY_CONFLICT',
          'The command ID was already used with a different transition',
        );
      }
      this.metrics.commitReplayed();
      return { ...prior.receipt, replayed: true };
    }

    const run = read ?? (await this.store.get(batch.orgId, batch.runId));
    if (run === null) throw new HarnessNotFoundError();
    const readMs = elapsedMs(started);
    validateCommitBatch(run, batch);
    const position = this.position(run, batch.attemptId, batch.fence);
    const appends: PreparedJournalAppend[] = [];
    const nodeEvents = transitionJournalEvents(run, batch.transition);
    let transcript: RunTranscript | undefined;
    try {
      if (batch.events.length > 0) {
        appends.push(
          await this.journals.prepare({
            ...position,
            writer: 'worker',
            events: batch.events,
          }),
        );
      }
      if (nodeEvents.length > 0) {
        appends.push(
          await this.journals.prepare({
            ...position,
            writer: 'node',
            events: nodeEvents,
          }),
        );
      }
      const terminal =
        batch.transition !== undefined &&
        TERMINAL_RUN_STATUSES.has(batch.transition.status);
      transcript = terminal
        ? await this.transcriptOf(
            run,
            [...batch.events, ...nodeEvents],
            batch.attemptId,
            batch.fence,
          )
        : undefined;
    } catch (error) {
      if (error instanceof PayloadStoreUnavailableError) {
        return this.yieldForPayloadStore(run, batch, error);
      }
      throw error;
    }
    const prepareMs = elapsedMs(started) - readMs;
    const receipt = await this.writeWithTranscript(
      run,
      transcript,
      batch.commandId,
      () => this.store.commit(batch, appends, transcript),
    );
    this.endedBy(run, receipt);
    this.metrics.approvalsRequested(
      run.orgId,
      batch.events.filter((event) => event.type === 'interaction.requested')
        .length,
    );
    // Node answers an ask a held grant covers before anyone is told the run waits for it (WP115-FU-2).
    await this.grantableAsks(receipt, batch);
    this.waitedBy(run, receipt);
    this.movedBy(run, receipt);
    this.asksMovedBy(run, receipt);
    // Off this commit's path: the commit that ends the run then reads only its own tail (QA22-F4).
    if (
      receipt.journal != null &&
      !TERMINAL_RUN_STATUSES.has(receipt.run.status)
    ) {
      this.runJournal.warm(run.orgId, run.runId, receipt.journal.lastSeq);
    }
    const ms = elapsedMs(started);
    const fields = {
      ...runLogFields(run),
      attemptId: batch.attemptId,
      fence: batch.fence,
      commandId: batch.commandId,
      seq: receipt.journal?.lastSeq ?? null,
      events: batch.events.length,
      ms,
      readMs,
      prepareMs,
      writeMs: ms - readMs - prepareMs,
    };
    if (batch.transition !== undefined) {
      if (batch.transition.detail?.kind === 'quarantine') {
        this.metrics.runQuarantined();
      }
      if (
        batch.transition.detail?.kind === 'yield' &&
        batch.transition.detail.reason === 'store_wait'
      ) {
        // The worker could not reach file storage (CH-09): Node never calls it, so this is how it learns.
        dependencyHealth.report('blob');
      }
      this.logger.info(
        'run.transition',
        harnessLogMeta('run.transition', {
          ...fields,
          from: run.status,
          to: receipt.run.status,
          detail: batch.transition.detail?.kind ?? null,
          reason:
            batch.transition.detail?.kind === 'suspend' ||
            batch.transition.detail?.kind === 'yield'
              ? batch.transition.detail.reason
              : (batch.transition.errorType ?? null),
          journaled: nodeEvents.map((event) => event.type),
          ...(TERMINAL_RUN_STATUSES.has(receipt.run.status)
            ? { leftInputs: leftInputs(receipt.run) }
            : {}),
        }),
      );
    } else {
      this.logger.debug('run.commit', harnessLogMeta('run.commit', fields));
    }
    return receipt;
  }

  /**
   * On a v2 run the control transaction also journals who cancelled, or the
   * interaction the resume resolves. The events are prepared against the
   * run at `expectedRevision`; the store refuses if it moved since.
   */
  /** `fence`: what the control's authority depends on, held inside its transaction (QA21-F2). */
  async control(
    command: ControlRun,
    fence?: CommitFence,
  ): Promise<CommitReceipt> {
    const receipt = await this.applyControl(command, fence);
    if (command.action === 'cancel' && receipt.run.status === 'cancelling') {
      await this.wakeCancelled(receipt.run);
    }
    // ADR-OPS-01 D6: the attempt a retire took back hears it now, not at its next renewal.
    if (command.action === 'suspend' && receipt.replayed !== true) {
      await this.wakeCancelled(receipt.run);
    }
    return receipt;
  }

  private async applyControl(
    command: ControlRun,
    fence?: CommitFence,
  ): Promise<CommitReceipt> {
    const prior = await this.store.findReceipt(
      command.orgId,
      command.runId,
      command.commandId,
    );
    if (prior !== null) {
      const replayed = await this.store.control(command, []);
      if (command.action === 'cancel') {
        await this.cancelChildren(
          replayed.run,
          command.commandId,
          command.by,
          'parent_cancelled',
        );
      }
      return replayed;
    }
    const run = await this.store.get(command.orgId, command.runId);
    if (run === null) throw new HarnessNotFoundError();
    if (run.harnessMajor !== 2)
      return this.store.control(command, [], undefined, fence);
    assertControlCurrent(run, command);
    const resolutions = controlResolutions(command);
    // ADR-OPS-01 D6: an operator's resume of a retired run resolves no interaction; the store holds it to one.
    const { grants: offers, declinable } =
      (command.action === 'resume' && command.cause === undefined) ||
      command.action === 'resolve'
        ? await this.assertInteractionOpen(run, command)
        : {
            grants: new Map<string, ConversationGrant>(),
            declinable: new Map<string, DeclinableAsk>(),
          };
    // A cancel that ends the run here (no worker holds it) closes the calls a crashed attempt left open (EV-06).
    const open =
      command.action === 'cancel' && !WORKER_OWNED_RUN_STATUSES.has(run.status)
        ? await this.openCalls(run)
        : [];
    const events =
      command.action === 'resolve' && resolutions.length > 0
        ? resolutionJournalEvents(
            run,
            resolutions,
            command.by ?? principalRefFor(run.actorId),
            offers,
            declinable,
          )
        : controlJournalEvents(run, command, open);
    const transcript = events.some((event) => event.type === 'run.cancelled')
      ? await this.transcriptOf(run, events)
      : undefined;
    this.logger.info(
      'run.control',
      harnessLogMeta('run.control', {
        ...runLogFields(run),
        commandId: command.commandId,
        action: command.action,
        interactionId: resolutions[0]?.interactionId ?? null,
        decision: resolutions[0]?.decision ?? null,
        ...(resolutions.length > 1 ? { resolved: resolutions.length } : {}),
        journaled: events.map((event) => event.type),
        ...(transcript !== undefined ? { leftInputs: leftInputs(run) } : {}),
      }),
    );
    const journal =
      events.length > 0
        ? [
            await this.journals.prepare({
              ...this.position(run),
              writer: 'node',
              events,
            }),
          ]
        : [];
    const receipt = await this.writeWithTranscript(
      run,
      transcript,
      command.commandId,
      () => this.store.control(command, journal, transcript, fence),
    );
    this.endedBy(run, receipt);
    this.movedBy(run, receipt);
    this.asksMovedBy(run, receipt);
    if (command.action === 'cancel') {
      await this.cancelChildren(
        run,
        command.commandId,
        command.by,
        'parent_cancelled',
      );
    }
    return receipt;
  }

  private async wakeCancelled(run: HarnessRun): Promise<void> {
    try {
      await this.wakeup.wake(run.runId);
    } catch (error) {
      dependencyHealth.report('kv');
      // The worker still sees run.cancel_requested at its next journal read.
      this.logger.warn(
        'run.cancel_wake_failed',
        harnessLogMeta('run.cancel_wake_failed', {
          ...runLogFields(run),
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
    }
  }

  /**
   * A parent's open children are cancelled with it, each by its own control
   * command (idempotent by id, so a replayed cancel cancels nothing twice);
   * a child that ended meanwhile is left alone. Recursive through `control`.
   */
  private async cancelChildren(
    parent: HarnessRun,
    commandId: string,
    by: string | undefined,
    reason: 'parent_cancelled' | 'parent_ended',
  ): Promise<number> {
    if (this.children === null || parent.harnessMajor !== 2) return 0;
    let cancelled = 0;
    for (const child of await this.children.openChildren(
      parent.orgId,
      parent.runId,
    )) {
      const childCommand = `${commandId}:child:${child.runId}`;
      try {
        await this.control({
          commandId: childCommand,
          dispatchCommandId: `${childCommand}:dispatch`,
          orgId: child.orgId,
          runId: child.runId,
          expectedRevision: child.revision,
          action: 'cancel',
          ...(by !== undefined ? { by } : {}),
        });
        cancelled += 1;
        this.logger.info(
          'child.cancelled',
          harnessLogMeta('child.cancelled', {
            ...runLogFields(child),
            parentRunId: parent.runId,
            reason,
          }),
        );
      } catch (error) {
        // Ended, or moved since the read: the orphan sweep settles what is left.
        this.logger.info(
          'child.cancel_skipped',
          harnessLogMeta('child.cancel_skipped', {
            ...runLogFields(child),
            parentRunId: parent.runId,
            reason: refusalCode(error),
          }),
        );
      }
    }
    return cancelled;
  }

  /**
   * A v2 run that ends leaves its transcript in the conversation, written by
   * the store in the same transaction as the ending (P1.8): projected here
   * from the run's journal plus what this commit appends.
   */
  private async transcriptOf(
    run: HarnessRun,
    appending: readonly JournalEventInput[],
    attemptId?: string,
    fence?: number,
  ): Promise<RunTranscript | undefined> {
    // A child's parent owns the user's message; the child never writes it.
    if (run.harnessMajor !== 2 || childOf(run) !== null) return undefined;
    const prior = await this.runEvents(run.orgId, run.runId);
    let seq = prior[prior.length - 1]?.seq ?? 0;
    const pending = appending.map((event): JournalEvent => ({
      ...event,
      orgId: run.orgId,
      sessionId: run.sessionId,
      streamId: journalStreamId(run),
      runId: run.runId,
      ...(attemptId !== undefined ? { attemptId } : {}),
      ...(fence !== undefined ? { fence } : {}),
      seq: (seq += 1),
      ts: '',
      writer: 'node',
    }));
    const events = [...prior, ...pending];
    const transcript = runTranscript(events, run.runId);
    if (transcript === null) return undefined;
    return { ...transcript, label: await this.turnLabel(run, events) };
  }

  /** The tool calls a v2 run left open, from its journal (EV-06). */
  private async openCalls(run: HarnessRun): Promise<readonly string[]> {
    if (run.harnessMajor !== 2) return [];
    return openToolCalls(await this.runEvents(run.orgId, run.runId), run.runId);
  }

  /** The run's journal, payloads resolved; only what this Node has not read comes from the store. */
  runEvents(orgId: string, runId: string): Promise<JournalEvent[]> {
    return this.runJournal.read(orgId, runId);
  }

  /**
   * The turn's label (P2.5a): the own work the worker pinned on its answer,
   * joined with its children's labels (read from their journals) and the
   * stored labels of the runs its model saw in full. Anything missing joins
   * as unknown.
   */
  private async turnLabel(
    run: HarnessRun,
    events: readonly JournalEvent[],
  ): Promise<TurnLabel> {
    try {
      return joinLabels([
        ownWorkLabel(events, run.runId),
        ...(await this.childLabels(run.orgId, events, run.runId, 0)),
        ...(await this.store.historyLabels(run)),
      ]);
    } catch (error) {
      // A label that cannot be joined is unknown; the run's ending never waits on it.
      this.logger.warn(
        'turn_labels.join_failed',
        harnessLogMeta('turn_labels.join_failed', {
          ...runLogFields(run),
          errorType: error instanceof Error ? error.name : typeof error,
          choice: 'unknown_label',
        }),
      );
      return joinLabels([null]);
    }
  }

  private async childLabels(
    orgId: string,
    events: readonly JournalEvent[],
    runId: string,
    depth: number,
  ): Promise<Array<TurnLabel | null>> {
    const children = spawnedChildren(events, runId);
    if (children.length === 0) return [];
    if (depth >= MAX_LABEL_CHILD_DEPTH) return [null];
    const labels: Array<TurnLabel | null> = [];
    for (const child of children) {
      const own = await this.runEvents(orgId, child);
      labels.push(
        joinLabels([
          ownWorkLabel(own, child),
          ...(await this.childLabels(orgId, own, child, depth + 1)),
        ]),
      );
    }
    return labels;
  }

  private async writeWithTranscript(
    run: HarnessRun,
    transcript: RunTranscript | undefined,
    commandId: string,
    write: () => Promise<CommitReceipt>,
  ): Promise<CommitReceipt> {
    let receipt: CommitReceipt;
    try {
      receipt = await write();
    } catch (error) {
      if (error instanceof TranscriptProjectionError) {
        const cause = error.cause;
        this.logger.error(
          'transcript.projection_failed',
          harnessLogMeta('transcript.projection_failed', {
            ...runLogFields(run),
            commandId,
            kind: transcript?.kind ?? null,
            errorType: cause instanceof Error ? cause.name : typeof cause,
            outcome: 'commit_rolled_back',
          }),
        );
      }
      throw error;
    }
    if (transcript !== undefined && receipt.replayed !== true) {
      this.labelled(receipt.run, transcript);
      this.logger.info(
        'transcript.projected',
        harnessLogMeta('transcript.projected', {
          ...runLogFields(run),
          commandId,
          kind: transcript.kind,
          ...(transcript.kind === 'answer'
            ? {
                parts: Array.isArray(transcript.completion['parts'])
                  ? (transcript.completion['parts'] as unknown[]).length
                  : 0,
                citations: Array.isArray(transcript.completion['citations'])
                  ? (transcript.completion['citations'] as unknown[]).length
                  : 0,
                questions: transcript.questions.length,
              }
            : { code: transcript.code }),
        }),
      );
    }
    return receipt;
  }

  /** The receipt a control command left, for a retried request; null: it never committed. */
  async controlReceipt(
    orgId: string,
    runId: string,
    commandId: string,
  ): Promise<CommitReceipt | null> {
    const prior = await this.store.findReceipt(orgId, runId, commandId);
    return prior?.receipt ?? null;
  }

  /**
   * A v2 run resumes only by resolving an interaction it opened and still waits on. Answers the conversation grant
   * each wanted ask offered (FLOWGRANT), which the grant then names, and the call each ask its own actor declines
   * holds (ADR-GRANT-01 D9), which the decline then names.
   */
  private async assertInteractionOpen(
    run: HarnessRun,
    command: ControlRun,
  ): Promise<{
    grants: Map<string, ConversationGrant>;
    declinable: Map<string, DeclinableAsk>;
  }> {
    const wanted = controlResolutions(command);
    if (wanted.length === 0) {
      throw new HarnessConflictError(
        'INVALID_TRANSITION',
        'A harness v2 run resumes by resolving the interaction it waits on',
      );
    }
    const ids = new Set(wanted.map((resolution) => resolution.interactionId));
    const open = new Set<string>();
    const offered = new Set<string>();
    const subjects = new Map<string, ConversationGrant>();
    const editable = new Set<string>();
    const asks = new Map<string, DeclinableAsk>();
    for (const event of await this.runEvents(run.orgId, run.runId)) {
      const id = event.data?.['interactionId'];
      if (typeof id !== 'string' || !ids.has(id)) continue;
      if (event.type === 'interaction.requested') open.add(id);
      if (event.type === 'interaction.resolved') open.delete(id);
      if (event.type === 'interaction.explained' && event.writer === 'worker') {
        // G-40, G-36: a grant never answers the org's policy webhook or an extension point's ask, so their ask is
        // allowed once at most (`offeredGrant`).
        const data = event.data ?? {};
        if (grantOffered(data)) offered.add(id);
        const grant = offeredGrant(data);
        if (grant !== null) subjects.set(id, grant);
        const asked = declinableAsk(data);
        if (asked !== null) asks.set(id, asked);
        if (event.data?.['editable'] === true) editable.add(id);
      }
    }
    const granted = new Map<string, ConversationGrant>();
    const declinable = new Map<string, DeclinableAsk>();
    // SEC-B-01, SEC-C-04 (ADR-GRANT-01 D9): a conversation decision is the run's actor's own; anyone else's (an
    // approver, the owner denying a collaborator's call, a reach gone wrong) answers the one ask they decide.
    const actorDecides =
      command.by === undefined || command.by === principalRefFor(run.actorId);
    for (const resolution of wanted) {
      if (!open.has(resolution.interactionId)) {
        throw new HarnessConflictError(
          'INTERACTION_NOT_OPEN',
          'The run has no open interaction with that id',
        );
      }
      // WP-69: the org's policy, not the worker's offer, decides whether an answer may hold for the conversation.
      if (resolution.scope !== undefined && run.conversationGrants === false) {
        throw new HarnessConflictError(
          'CONVERSATION_GRANT_NOT_OFFERED',
          CONVERSATION_GRANTS_OFF_MESSAGE,
        );
      }
      if (
        resolution.scope !== undefined &&
        !offered.has(resolution.interactionId)
      ) {
        throw new HarnessConflictError(
          'CONVERSATION_GRANT_NOT_OFFERED',
          'This call can only be allowed once',
        );
      }
      if (resolution.scope !== undefined && !actorDecides) {
        throw new HarnessConflictError(
          'CONVERSATION_GRANT_NOT_OFFERED',
          CONVERSATION_GRANT_ACTOR_ONLY_MESSAGE,
        );
      }
      const asked = asks.get(resolution.interactionId);
      if (
        resolution.decision === 'denied' &&
        resolution.channel !== 'system' &&
        actorDecides &&
        asked !== undefined
      ) {
        declinable.set(resolution.interactionId, asked);
      }
      const grant = subjects.get(resolution.interactionId);
      if (resolution.scope !== undefined && grant !== undefined) {
        granted.set(resolution.interactionId, grant);
      }
      // AP-06: an edit is approved once, and only where the worker said it would run the edit.
      if (
        resolution.edits !== undefined &&
        (resolution.decision !== 'approved' ||
          resolution.scope !== undefined ||
          !editable.has(resolution.interactionId))
      ) {
        throw new HarnessConflictError(
          'EDIT_NOT_OFFERED',
          'This call can only be approved as it is',
        );
      }
    }
    return { grants: granted, declinable };
  }

  /** DECIDE409: the run's decided interactions, each with its journaled decision. */
  async interactionDecisions(
    orgId: string,
    runId: string,
  ): Promise<Map<string, InteractionDecision>> {
    const decided = new Map<string, InteractionDecision>();
    for (const event of await this.runEvents(orgId, runId)) {
      const id = event.data?.['interactionId'];
      const decision = event.data?.['decision'];
      if (
        event.type === 'interaction.resolved' &&
        typeof id === 'string' &&
        typeof decision === 'string'
      ) {
        decided.set(id, decision as InteractionDecision);
      }
    }
    return decided;
  }

  /**
   * The tool whose rule the person may save from this interaction's card
   * (BUGS-B): the `ruleGrant` the worker wrote with its reason, null when it
   * offered none.
   */
  async ruleGrantOffered(
    orgId: string,
    runId: string,
    interactionId: string,
  ): Promise<RuleGrantTarget | null> {
    let offered: RuleGrantTarget | null = null;
    for (const event of await this.runEvents(orgId, runId)) {
      const data = event.data ?? {};
      if (
        event.type !== 'interaction.explained' ||
        event.writer !== 'worker' ||
        data['interactionId'] !== interactionId
      ) {
        continue;
      }
      const grant = data['ruleGrant'] as Record<string, unknown> | undefined;
      const instanceId = grant?.['instanceId'];
      const toolName = grant?.['toolName'];
      offered =
        typeof instanceId === 'string' && typeof toolName === 'string'
          ? { instanceId, toolName }
          : null;
    }
    return offered;
  }

  /** Deterministic from the run and fence, so a retried yield hashes the same. */
  private async withTransitionOutbox(batch: CommitBatch): Promise<CommitBatch> {
    if (batch.transition?.detail?.kind !== 'yield') return batch;
    const run = await this.store.get(batch.orgId, batch.runId);
    if (run === null) throw new HarnessNotFoundError();
    const outbox = transitionOutbox(
      { harnessMajor: run.harnessMajor, runId: run.runId, fence: batch.fence },
      batch.transition,
    );
    return { ...batch, outbox: [...batch.outbox, ...outbox] };
  }

  /**
   * Primary, majority-committed journal for the run's latest attempt. A
   * claim-checked payload that is gone fails the run (terminal, surfaced)
   * instead of letting every later claim retry it [R-5].
   */
  async readRunJournal(
    query: ReadRunJournal,
    read?: HarnessRun | null,
  ): Promise<JournalEvent[]> {
    const run = read ?? (await this.store.get(query.orgId, query.runId));
    if (run === null) throw new HarnessNotFoundError();
    const owning = attemptOwnsRun(run, query);
    if (!attemptMayReadRun(run, query)) {
      throw new HarnessConflictError(
        'STALE_ATTEMPT',
        'Worker no longer owns this run',
      );
    }
    const limit = Math.min(Math.max(query.limit, 1), MAX_JOURNAL_PAGE);
    try {
      const events =
        query.scope === 'run'
          ? await this.journal.readRun(
              run.orgId,
              run.runId,
              query.afterSeq,
              limit,
            )
          : await this.journal.readStream(
              run.orgId,
              journalStreamId(run),
              query.afterSeq,
              limit,
            );
      return await this.journals.resolve(events);
    } catch (error) {
      if (error instanceof JournalPayloadMissingError) {
        await this.failRun(
          run,
          JOURNAL_PAYLOAD_MISSING,
          owning ? query.attemptId : undefined,
        );
      }
      if (error instanceof PayloadStoreUnavailableError && owning) {
        return this.yieldForPayloadStore(
          run,
          {
            orgId: run.orgId,
            runId: run.runId,
            workerId: query.workerId,
            attemptId: query.attemptId,
            fence: query.fence,
          },
          error,
        );
      }
      throw error;
    }
  }

  /**
   * A claim-checked payload could not be stored or read (REPORT 6.17): the
   * run yields with a backoff instead of failing or holding a worker slot.
   * Nothing of the refused request was journaled; the next claim folds from
   * the journal. The worker is told it lost the attempt (a code every worker
   * build already treats as "stop"); if the yield itself cannot be written
   * the original 503 stands and the worker retries.
   */
  private async yieldForPayloadStore(
    run: HarnessRun,
    attempt: Pick<
      CommitBatch,
      'orgId' | 'runId' | 'workerId' | 'attemptId' | 'fence'
    >,
    cause: PayloadStoreUnavailableError,
  ): Promise<never> {
    const fields = {
      ...runLogFields(run),
      attemptId: attempt.attemptId,
      fence: attempt.fence,
      operation: cause.operation,
    };
    let notBefore: string;
    try {
      notBefore = new Date(
        (await this.store.now(run.orgId)) + PAYLOAD_STORE_BACKOFF_MS,
      ).toISOString();
      const batch = await this.withTransitionOutbox({
        ...attempt,
        commandId: `payload-yield:${attempt.attemptId}:${String(attempt.fence)}`,
        events: [],
        legacyEvents: [],
        outbox: [],
        transition: {
          status: 'queued',
          detail: {
            kind: 'yield',
            reason: 'payload_store_unavailable',
            notBefore,
          },
        },
      });
      validateCommitBatch(run, batch);
      const events = transitionJournalEvents(run, batch.transition);
      await this.store.commit(batch, [
        await this.journals.prepare({
          ...this.position(run, attempt.attemptId, attempt.fence),
          writer: 'node',
          events,
        }),
      ]);
    } catch (error) {
      this.logger.warn(
        'run.payload_store_unavailable',
        harnessLogMeta('run.payload_store_unavailable', {
          ...fields,
          outcome: 'retry',
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
      throw cause;
    }
    this.logger.warn(
      'run.payload_store_unavailable',
      harnessLogMeta('run.payload_store_unavailable', {
        ...fields,
        outcome: 'yielded',
        notBefore,
      }),
    );
    throw new HarnessConflictError(
      'STALE_ATTEMPT',
      'The journal payload store is unavailable; the run yielded',
    );
  }

  /**
   * The events a commit receipt appended, payloads resolved, so the worker
   * folds them without another round trip. Through the Node's reader: the
   * read-ahead the commit started is this read (PERF-D), and what it brings
   * is not read again by the commit that ends the run.
   */
  async receiptEvents(
    orgId: string,
    receipt: CommitReceipt,
  ): Promise<JournalEvent[]> {
    const range = receipt.journal;
    if (range === undefined || range === null) return [];
    const events = await this.runJournal.read(
      orgId,
      receipt.run.runId,
      range.firstSeq - 1,
      range.lastSeq,
    );
    return events.filter((event) => event.seq <= range.lastSeq);
  }

  /** The run with its pinned spec as stored (null: not pinned yet, or a v1 run). */
  async pinned(
    orgId: string,
    runId: string,
  ): Promise<{ run: HarnessRun; pinned: PinnedRunSpec | null } | null> {
    const subject = await this.store.readClaimSubject(orgId, runId);
    return subject === null
      ? null
      : { run: subject.run, pinned: subject.pinned };
  }

  /**
   * WP-64: the items the person chose not to connect, or did not in time, anywhere in the run's tree (its root, its
   * parent, a sibling, a sub-agent), which a claim names so no run of the tree asks for one again. Each run of a tree
   * journals on its own stream and a worker reads only its own, so Node, which reads them all, names them. A run
   * alone in its tree reads its own skips itself: nothing is read for it here.
   */
  async treeSkippedConnects(run: HarnessRun): Promise<string[]> {
    if (run.parent === undefined && (run.children?.spawned ?? 0) === 0)
      return [];
    const tree = runTreeOf(
      await this.store.sessionRunParents(run.orgId, run.sessionId),
      run.runId,
    );
    const skipped = new Set<string>();
    for (const runId of tree) {
      for (let after = 0; ;) {
        const page = await this.journal.readRun(
          run.orgId,
          runId,
          after,
          SKIP_SCAN_PAGE,
        );
        for (const instance of skippedConnectsIn(page)) skipped.add(instance);
        if (page.length < SKIP_SCAN_PAGE) break;
        after = page[page.length - 1]?.seq ?? after;
      }
    }
    return [...skipped].sort();
  }

  /** FLOWGRANT: the conversation grants a run holds and its open asks one could answer, from its journal. */
  /**
   * A run's grants, declines and open asks (ADR-GRANT-01): only its actor's decisions count (D9). With `session`,
   * also what its person decided anywhere the conversation's root stream records: its root runs' own decisions and
   * the copies its jobs' decisions left there, so a later turn or a later job is answered as an open run was.
   */
  async grantState(
    orgId: string,
    runId: string,
    options: { session?: boolean } = {},
  ): Promise<RunGrantState> {
    const run = await this.store.get(orgId, runId);
    if (run === null) throw new HarnessNotFoundError();
    const principal = principalRefFor(run.actorId);
    const own = runGrantState(await this.runEvents(orgId, runId), principal);
    if (options.session !== true) return own;
    const earlier = sessionDecisions(
      await this.streamEvents(orgId, run.sessionId),
      principal,
    );
    return {
      ...own,
      held: widestGrants([...earlier.held, ...own.held]),
      declined: [
        ...earlier.declined.filter(
          (decline) => coveringDecline(own.declined, decline) === undefined,
        ),
        ...own.declined,
      ],
    };
  }

  /** A stream's whole journal, paged. */
  private async streamEvents(
    orgId: string,
    streamId: string,
  ): Promise<JournalEvent[]> {
    const events: JournalEvent[] = [];
    let after = 0;
    for (;;) {
      const page = await this.journal.readStream(
        orgId,
        streamId,
        after,
        MAX_JOURNAL_PAGE,
      );
      events.push(...page);
      if (page.length < MAX_JOURNAL_PAGE) return events;
      after = page[page.length - 1]?.seq ?? after;
    }
  }

  /** FLOWGRANT: the conversation's runs that have not ended, each of which a grant given in any of them reaches. */
  async openSessionRuns(
    orgId: string,
    sessionId: string,
  ): Promise<HarnessRun[]> {
    const open: HarnessRun[] = [];
    for (const { runId } of await this.store.sessionRunParents(
      orgId,
      sessionId,
    )) {
      const run = await this.store.get(orgId, runId);
      if (
        run !== null &&
        run.harnessMajor === 2 &&
        !TERMINAL_RUN_STATUSES.has(run.status)
      ) {
        open.push(run);
      }
    }
    return open;
  }

  async get(orgId: string, runId: string): Promise<HarnessRun | null> {
    const run = await this.store.get(orgId, runId);
    return run === null ? null : this.inputs.open(run);
  }

  /** A completed run's suggested next questions (UX5); the first write wins, a retry reads it back. */
  async storeFollowups(
    orgId: string,
    runId: string,
    followups: readonly Followup[],
  ): Promise<Followup[]> {
    const write = await this.store.storeFollowups(orgId, runId, followups);
    if (write === null) throw new HarnessNotFoundError();
    if (write.outcome === 'not_completed') throw new RunNotCompletedError();
    if (write.outcome === 'stored') {
      this.logger.info(
        'run.followups_stored',
        harnessLogMeta('run.followups_stored', {
          orgId,
          runId,
          count: write.followups.length,
        }),
      );
    }
    return write.followups;
  }

  /**
   * What the grant auditor (P9) checks a run's executed calls against: the
   * pinned grant (tool ids, model route ids) and the run's tool intents. Ids
   * only, never arguments or results. Null when the run does not exist.
   */
  async auditEvidence(
    orgId: string,
    runId: string,
  ): Promise<RunAuditEvidence | null> {
    const subject = await this.store.readClaimSubject(orgId, runId);
    if (subject === null) return null;
    const events = await this.runEvents(orgId, runId);
    const spec = subject.pinned?.spec ?? null;
    const account =
      this.budgets === null ? null : await this.budgets.account(orgId, runId);
    return {
      orgId,
      runId,
      pinned: spec !== null,
      toolIds:
        spec === null ? [] : spec.grants.tools.map((tool) => tool.toolId),
      routeIds:
        spec === null
          ? []
          : spec.model.policy.map((route) => modelRouteId(route)),
      intents: events
        .filter((event) => event.type === 'tool.intent')
        .map((event) => {
          const data = (event.data ?? {}) as {
            invocationId?: unknown;
            toolId?: unknown;
          };
          return {
            invocationId:
              typeof data.invocationId === 'string' ? data.invocationId : '',
            toolId: typeof data.toolId === 'string' ? data.toolId : '',
            attemptId: event.attemptId ?? null,
          };
        }),
      ended: TERMINAL_RUN_STATUSES.has(subject.run.status),
      usage: events.flatMap((event) => {
        const found = auditUsage(event);
        return found === null ? [] : [found];
      }),
      ledger:
        account === null
          ? null
          : {
              used:
                account.used === undefined
                  ? null
                  : { ...ZERO_USAGE, ...account.used },
              reserved: { ...account.reserved },
            },
    };
  }

  /** What the run may do, from its pinned spec (SEC1); null when the run does not exist. */
  async effectivePolicy(
    orgId: string,
    runId: string,
  ): Promise<EffectivePolicy | null> {
    const subject = await this.store.readClaimSubject(orgId, runId);
    if (subject === null) return null;
    const policy = effectivePolicyOf(
      subject.pinned?.spec ?? null,
      subject.resolved ?? null,
    );
    const events = await this.runEvents(orgId, runId);
    const blocked = blockedRequestsOf(
      events.filter((event) => event.type === 'gateway.refused'),
    );
    const held = heldRequestsOf(
      events.filter((event) => HELD_REQUEST_EVENTS.has(event.type)),
    );
    return {
      ...policy,
      ...(blocked.length > 0 ? { blockedRequests: blocked } : {}),
      ...(held.length > 0 ? { heldRequests: held } : {}),
    };
  }

  /** The run's pin and what it held or had refused (CPD-5); null when the run has no pin. */
  async resolvedView(
    orgId: string,
    runId: string,
  ): Promise<ResolvedRunView | null> {
    const subject = await this.store.readClaimSubject(orgId, runId);
    const resolved = subject?.resolved ?? null;
    if (resolved === null) return null;
    const events = await this.runEvents(orgId, runId);
    return {
      resolved,
      liveApplied: [],
      heldRequests: heldRequestsOf(
        events.filter((event) => HELD_REQUEST_EVENTS.has(event.type)),
      ),
      blockedRequests: blockedRequestsOf(
        events.filter((event) => event.type === 'gateway.refused'),
      ),
    };
  }

  getActive(orgId: string, sessionId: string): Promise<HarnessRun | null> {
    return this.store.getActive(orgId, sessionId);
  }

  readEvents(
    orgId: string,
    sessionId: string,
    after = 0,
    limit = 200,
  ): Promise<HarnessEvent[]> {
    return this.store.readEvents(
      orgId,
      sessionId,
      after,
      Math.min(Math.max(limit, 1), 500),
    );
  }

  /**
   * Expiry handling, then inline runs nobody claimed within `T_CLAIM_MS`
   * handed to dispatched workers (REPORT 6.2.1).
   */
  async sweep(limit: number, expired?: SweptRunObserver): Promise<number> {
    const bound = Math.min(Math.max(limit, 1), MAX_SWEEP_LIMIT);
    const observed: SweptTranscripts =
      expired === undefined
        ? this.sweptTranscripts
        : {
            ...this.sweptTranscripts,
            expired: (run, to, reason) => {
              this.sweptTranscripts.expired?.(run, to, reason);
              expired(run, reason);
            },
          };
    const swept = await this.store.sweep(bound, observed);
    const orphans =
      bound - swept > 0 ? await this.cancelOrphans(bound - swept) : 0;
    const left = bound - swept - orphans;
    const promoted =
      left > 0 ? await this.store.promoteInline(T_CLAIM_MS, left) : 0;
    const unstranded = await this.unstrand(left - promoted);
    const notStarted = await this.failUnclaimed(left - promoted - unstranded);
    if (promoted > 0) {
      this.logger.info(
        'run.inline_promoted',
        harnessLogMeta('run.inline_promoted', {
          promoted,
          reason: 'not_claimed_within_t_claim',
          tClaimMs: T_CLAIM_MS,
        }),
      );
    }
    return swept + orphans + promoted + unstranded + notStarted;
  }

  /**
   * Interactive runs no worker claimed within the claim deadline (UX3): a
   * person is watching, so the run ends now with a readable reason instead
   * of waiting out its hour. Never claimed: no effect can have happened.
   */
  private async failUnclaimed(limit: number): Promise<number> {
    if (limit <= 0) return 0;
    const due = await this.store.unclaimedRuns(
      INTERACTIVE_CLAIM_DEADLINE_MS,
      limit,
    );
    for (const run of due) {
      await forEnumeratedOrg(run.orgId, SWEEP_JOB, () =>
        this.failRun(run, NOT_STARTED),
      );
      this.logger.warn(
        'run.not_started',
        harnessLogMeta('run.not_started', {
          ...runLogFields(run),
          reason: NOT_STARTED,
          deadlineMs: INTERACTIVE_CLAIM_DEADLINE_MS,
        }),
      );
    }
    return due.length;
  }

  /**
   * A worker running a run that is being cancelled hears it at once (UX3,
   * R1 F5): the same channel a resolved approval wakes, which the driver
   * races its model call and reads against.
   */
  wakeOnCancel(wakeup: RunWakeup): void {
    this.wakeup = wakeup;
  }

  /** `availability` decides which features admission may require and which waits the sweeper releases (DUR2). */
  watchWorkerFeatures(
    availability: WorkerFeatureAvailability,
    guarded: readonly WorkerFeature[] = GUARDED_WORKER_FEATURES,
  ): void {
    this.workerFeatures = availability;
    this.guardedFeatures = guarded;
  }

  /**
   * Queued runs waiting for a feature no live worker advertises, waited past the
   * window a returning worker would have needed to show up: a run no claim pinned
   * yet drops the requirement and runs as before it (an older worker takes it); a
   * pinned one fails `no_compatible_worker`. Neither waits for its deadline.
   */
  private async unstrand(limit: number): Promise<number> {
    const availability = this.workerFeatures;
    if (availability === null || limit <= 0) return 0;
    let released = 0;
    for (const feature of this.guardedFeatures) {
      if (released >= limit || (await availability.live(feature))) continue;
      const stranded = await this.store.strandedRuns(
        feature,
        WORKER_FEATURE_WINDOW_MS,
        limit - released,
      );
      for (const { run, pinned } of stranded) {
        const fields = {
          ...runLogFields(run),
          feature,
          waitedMs: Date.now() - (run.waitingForWorker?.since ?? Date.now()),
        };
        if (!pinned) {
          const relaxed = await forEnumeratedOrg(run.orgId, SWEEP_JOB, () =>
            this.store.relaxRequirement(
              run.orgId,
              run.runId,
              run.revision,
              feature,
            ),
          );
          if (!relaxed) continue;
          availability.metrics.stranded(feature, 'relaxed');
          this.logger.info(
            'run.requirement_relaxed',
            harnessLogMeta('run.requirement_relaxed', {
              ...fields,
              reason: 'no_live_worker',
            }),
          );
        } else {
          await forEnumeratedOrg(run.orgId, SWEEP_JOB, () =>
            this.failRun(run, NO_COMPATIBLE_WORKER),
          );
          availability.metrics.stranded(feature, 'failed');
          this.logger.warn(
            'run.stranded_failed',
            harnessLogMeta('run.stranded_failed', {
              ...fields,
              reason: NO_COMPATIBLE_WORKER,
            }),
          );
        }
        released += 1;
      }
    }
    return released;
  }

  /** Children whose parent ended without cancelling them (a crash between the two). */
  private async cancelOrphans(limit: number): Promise<number> {
    if (this.children === null) return 0;
    let cancelled = 0;
    for (const child of await this.children.orphanedChildren(limit)) {
      const parentRunId = child.parent?.runId ?? null;
      const commandId = `orphan:${child.runId}:${String(child.revision)}`;
      try {
        await forEnumeratedOrg(child.orgId, SWEEP_JOB, () =>
          this.control({
            commandId,
            dispatchCommandId: `${commandId}:dispatch`,
            orgId: child.orgId,
            runId: child.runId,
            expectedRevision: child.revision,
            action: 'cancel',
          }),
        );
        cancelled += 1;
        this.logger.info(
          'child.cancelled',
          harnessLogMeta('child.cancelled', {
            ...runLogFields(child),
            parentRunId,
            reason: 'parent_ended',
          }),
        );
      } catch (error) {
        this.logger.info(
          'child.cancel_skipped',
          harnessLogMeta('child.cancel_skipped', {
            ...runLogFields(child),
            parentRunId,
            reason: refusalCode(error),
          }),
        );
      }
    }
    return cancelled;
  }

  // Each callback runs as the swept run's org: the PostgreSQL store binds it from its own cross-org read; the Mongo
  // store's sweep binds none, and the run-end listeners these start must still run as the run's org.
  private readonly sweptTranscripts: SweptTranscripts = {
    expired: (run, to, reason) => {
      forEnumeratedOrg(run.orgId, SWEEP_JOB, () => {
        this.ended(run, { ...run, status: to });
        this.asksMoved(run, { ...run, status: to });
      });
      this.logger.info(
        'sweeper.expired',
        harnessLogMeta('sweeper.expired', {
          ...runLogFields(run),
          from: run.status,
          to,
          reason,
          leftInputs: leftInputs(run),
        }),
      );
    },
    openCalls: (run) =>
      forEnumeratedOrg(run.orgId, SWEEP_JOB, () => this.openCalls(run)),
    project: (run, appending) =>
      forEnumeratedOrg(run.orgId, SWEEP_JOB, () =>
        this.sweptTranscript(run, appending),
      ),
    settled: (run, transcript, outcome, cause) => {
      this.logSweptTranscript(run, transcript, outcome, cause);
      if (outcome === 'written')
        forEnumeratedOrg(run.orgId, SWEEP_JOB, () => {
          this.labelled(run, transcript);
        });
    },
  };

  /** A projection that cannot be built leaves the swept run its status only. */
  private async sweptTranscript(
    run: HarnessRun,
    appending: readonly JournalEventInput[],
  ): Promise<RunTranscript | undefined> {
    try {
      return await this.transcriptOf(run, appending);
    } catch (error) {
      this.logger.error(
        'transcript.projection_failed',
        harnessLogMeta('transcript.projection_failed', {
          orgId: run.orgId,
          sessionId: run.sessionId,
          runId: run.runId,
          errorType: error instanceof Error ? error.name : typeof error,
          outcome: 'status_only',
        }),
      );
      return undefined;
    }
  }

  private logSweptTranscript(
    run: HarnessRun,
    transcript: RunTranscript,
    outcome: SweptTranscriptOutcome,
    cause: unknown,
  ): void {
    const fields = {
      orgId: run.orgId,
      sessionId: run.sessionId,
      runId: run.runId,
      code:
        transcript.kind === 'answer'
          ? typeof transcript.completion['errorCode'] === 'string'
            ? transcript.completion['errorCode']
            : null
          : transcript.code,
    };
    if (outcome === 'written') {
      this.logger.info(
        'projection.deadline_failure',
        harnessLogMeta('projection.deadline_failure', fields),
      );
    } else if (outcome === 'session_missing') {
      this.logger.warn(
        'projection.deadline_failure_skipped',
        harnessLogMeta('projection.deadline_failure_skipped', {
          ...fields,
          reason: 'session_missing',
        }),
      );
    } else {
      this.logger.error(
        'transcript.projection_failed',
        harnessLogMeta('transcript.projection_failed', {
          ...fields,
          errorType: cause instanceof Error ? cause.name : typeof cause,
          outcome: 'status_only',
        }),
      );
    }
  }

  /** `holder`: the attempt this call acts for; without one, a run another attempt still holds is left to it. */
  private async failRun(
    run: HarnessRun,
    errorType: string,
    holder?: string,
  ): Promise<boolean> {
    const journal =
      run.harnessMajor === 2
        ? [
            await this.journals.prepare({
              ...this.position(run),
              writer: 'node',
              events: transitionJournalEvents(run, {
                expectedRevision: run.revision,
                status: 'failed',
                errorType,
              }),
            }),
          ]
        : [];
    if (
      !(await this.store.failRun(
        run.orgId,
        run.runId,
        errorType,
        journal,
        holder,
      ))
    ) {
      return false;
    }
    this.ended(run, { ...run, status: 'failed' });
    this.asksMoved(run, { ...run, status: 'failed' });
    return true;
  }

  private position(
    run: HarnessRun,
    attemptId?: string,
    fence?: number,
  ): JournalPosition {
    return {
      orgId: run.orgId,
      sessionId: run.sessionId,
      streamId: journalStreamId(run),
      runId: run.runId,
      ...(attemptId !== undefined ? { attemptId } : {}),
      ...(fence !== undefined ? { fence } : {}),
    };
  }
}

/**
 * The claimed run as the worker reads it: with the history its document only names, and the locale only when the
 * run requires the worker feature that reads it (G-52).
 */
function withHistory(
  claimed: ClaimedRun,
  history: HistoryTurnEntry[] | null,
): ClaimedRun {
  const input = workerInput(claimed.run);
  if (input === undefined) return claimed;
  if (history === null && input === claimed.run.input) return claimed;
  return {
    ...claimed,
    run: {
      ...claimed.run,
      input:
        history === null ? input : { ...input, previousConversations: history },
    },
  };
}

/** How a durable run's ending reads as a chat turn; a sweep or Node-side failure is `failed`. */
const turnOutcome = (run: HarnessRun): ChatTurnOutcome => {
  if (run.status === 'cancelled') return 'cancelled';
  if (run.status !== 'completed') return 'failed';
  return run.outcome === 'needs_input' ? 'needs_input' : 'answered';
};

/** A listener's push is a hint: one that throws never stops the others, and the next read is the record. */
function tell(
  listeners: ReadonlyArray<(run: HarnessRun) => void>,
  run: HarnessRun,
): void {
  for (const listener of listeners) {
    try {
      listener(run);
    } catch {
      // The next read is the record.
    }
  }
}
