import type { RunParent } from '../domain/answered-run';
import type { CommitFence } from './commit-fence';
import { RunTranscript } from './conversation-writer';
import {
  ClaimCandidate,
  ClaimedRun,
  ClaimPinning,
  ClaimRole,
  ClaimRun,
  CommitBatch,
  CommitReceipt,
  ControlRun,
  HarnessEvent,
  HarnessRun,
  ReleaseAttempt,
  RenewAttempt,
  RunStatus,
} from '../domain/execution';
import { Followup, FollowupsWrite } from '../domain/followups';
import { JournalEventInput, PreparedJournalAppend } from '../domain/journal';
import { PinnedRunSpec, SpecSubject } from '../domain/run-spec';
import {
  ClaimingWorker,
  RunRequirements,
  WorkerFeature,
} from '../domain/worker-compat';
import type { TurnLabel } from '../domain/turn-labels';
import type { ClaimOrder } from '../domain/priority-class';
import type { RunHistoryRef } from '../domain/run-history';

/** A queued run a claimer was filtered from, marked for it (RU-2e). */
export interface WaitingRun {
  orgId: string;
  runId: string;
  sessionId: string;
  requires: RunRequirements;
}

/** A queued run no live worker can serve (DUR2); `pinned`: a claim already pinned its spec. */
export interface StrandedRun {
  run: HarnessRun;
  pinned: boolean;
}

/** Which pending runs the approvals queue reads (P2.11e); the org is always the caller's. */
export interface PendingApprovalFilter {
  actorId?: string;
  agentKey?: string;
  limit: number;
}

/** G-13: whose questions the inbox lists; a question is only ever for the person the run acts for. */
export interface UnansweredQuestionFilter {
  actorId: string;
  agentKey?: string;
  limit: number;
}

export interface StoredReceipt {
  payloadHash: string;
  receipt: CommitReceipt;
}

/**
 * `session_missing`: the run's conversation is gone, so only its status was
 * set; `write_failed`: the write rolled the sweep back, which then ended the
 * run with its status only.
 */
export type SweptTranscriptOutcome =
  'written' | 'session_missing' | 'write_failed';

export type SweepReason =
  | 'lease_expired'
  | 'cancel_lease_expired'
  | 'deadline_exceeded'
  | 'worker_lost';

/**
 * What a run the sweeper fails at its deadline leaves in its conversation
 * (PA-10), and what the sweeper did to each run.
 */
export interface SweptTranscripts {
  /** Once the sweeper's transition of `run` (as it was) committed. */
  expired?(run: HarnessRun, to: RunStatus, reason: SweepReason): void;
  /**
   * The tool calls the run left open (EV-06): the sweeper closes each with a
   * cancelled outcome ahead of the run's terminal event. Absent: none.
   */
  openCalls?(run: HarnessRun): Promise<readonly string[]>;
  /** The run's transcript once `appending` is journaled; undefined: status only. */
  project(
    run: HarnessRun,
    appending: readonly JournalEventInput[],
  ): Promise<RunTranscript | undefined>;
  /** Once the sweep of `run` committed. */
  settled(
    run: HarnessRun,
    transcript: RunTranscript,
    outcome: SweptTranscriptOutcome,
    cause?: unknown,
  ): void;
}

/** A run that ended, with the turn label its transcript stored (null: none was). */
export interface RunEnding {
  run: HarnessRun;
  label: TurnLabel | null;
}

export type ClaimSubject = SpecSubject & {
  pinned: PinnedRunSpec | null;
  /** Where the run's history is kept (QA22-F2); absent or null: inline in its input, or it has none. */
  history?: RunHistoryRef | null;
};

/** ADR-OPS-01: which open runs an operator control looks at; every member narrows. */
export interface OpenRunFilter {
  orgId?: string;
  sessionId?: string;
  agentKey?: string;
  /** The run's pinned engine profile is this version. */
  engine?: { kind: string; version: string };
  /** Only runs an engine executes (they hold a sandbox). */
  engineOnly?: boolean;
  /** Only paused runs suspended for this reason (ADR-OPS-01 D6: `retired`). */
  suspendedFor?: string;
}

export interface OpenRun {
  run: HarnessRun;
  /** The engine the run's admission pinned; null for a loop run. */
  engine: { kind: string; version: string } | null;
}

/** Runs that have not ended, oldest first, for the operator controls' enforcement and the admins' list. */
export interface OperatorRuns {
  openRuns(filter: OpenRunFilter, limit: number): Promise<OpenRun[]>;
}

/** SEC-AZ-08: one person's runs that have not ended, in every session of the org (a deactivation cancels them). */
export interface ActorRuns {
  activeRunsOfActor(orgId: string, actorId: string): Promise<HarnessRun[]>;
}

/**
 * Run state after admission. Runs are created only by RunAdmissionStore.admit,
 * whose receipts are the single idempotency namespace for new runs.
 */
export interface ExecutionStore {
  /**
   * Whether the run's conversation was deleted (QA21-F4): a commit the store refused for a run that erasure
   * stopped answers this cause, as one whose transcript found no session does.
   */
  conversationDeleted(run: HarnessRun): Promise<boolean>;
  /**
   * The store's clock (DB time), epoch ms: what an instant the store compares
   * with its own clock (a yield's `notBefore`) is minted from, so a Node
   * replica's stepped clock moves no retry (CH-14).
   */
  now(orgId: string): Promise<number>;
  // Lease and deadline predicates use the store's own clock (DB time), never
  // the calling process's.
  /**
   * On a harness major 2 run the claim transaction also appends `run.claimed`
   * and, when the run has no pin yet, pins `pinning` and appends its
   * `run.spec_pinned`. An unpinned v2 run claimed without `pinning` is refused.
   */
  claim(command: ClaimRun, pinning?: ClaimPinning): Promise<ClaimedRun>;
  /**
   * Queued harness v2 runs this role may claim now, highest priority class
   * first and oldest-first within a class (G-45): `notBefore` passed, and for
   * a dispatched claimer an inline run only once it waited `tClaimMs` for its
   * inline claimer. A read, not a claim.
   */
  nextClaimable(
    role: ClaimRole,
    tClaimMs: number,
    limit: number,
    /** Only runs this worker can run (RU-2); absent: every run. */
    worker?: ClaimingWorker,
    /** `floor`: oldest-first whatever the class; `above`: only classes higher than it. */
    order?: ClaimOrder,
  ): Promise<ClaimCandidate[]>;
  /**
   * Due runs for this role that `worker` cannot run and nobody marked yet:
   * each is marked `waitingForWorker` (once) and returned, so the filter
   * that hid it from the queue read is logged (RU-2e). Never fails a run.
   */
  markWaiting(
    role: ClaimRole,
    tClaimMs: number,
    limit: number,
    worker: ClaimingWorker,
  ): Promise<WaitingRun[]>;
  /** One queued run a claim by id was filtered from; true when this call marked it. */
  markRunWaiting(
    orgId: string,
    runId: string,
    requires: RunRequirements,
  ): Promise<boolean>;
  /**
   * Queued runs that require `feature` and that a worker was refused for at
   * least `waitedMs` ago (DB time), oldest wait first, with whether a claim
   * already pinned their spec (DUR2: no live worker serves the feature).
   */
  strandedRuns(
    feature: WorkerFeature,
    waitedMs: number,
    limit: number,
  ): Promise<StrandedRun[]>;
  /**
   * Drops `feature` from a queued, never-pinned run's requirements and clears
   * its wait, so a worker without the feature may claim it (DUR2). A CAS on
   * `revision`: false when the run moved, was pinned or no longer requires it.
   */
  relaxRequirement(
    orgId: string,
    runId: string,
    revision: number,
    feature: WorkerFeature,
  ): Promise<boolean>;
  /**
   * Back to `queued` (claimable at once) without a transition; wakes workers.
   * A release before the runtime was built (`drain`) takes back its attempt.
   */
  release(command: ReleaseAttempt): Promise<HarnessRun>;
  /** What admission fixed for the run, and its pin; null when absent. */
  readClaimSubject(orgId: string, runId: string): Promise<ClaimSubject | null>;
  renew(command: RenewAttempt): Promise<HarnessRun>;
  /** An earlier command's receipt, read before validation so a retry always replays. */
  findReceipt(
    orgId: string,
    runId: string,
    commandId: string,
  ): Promise<StoredReceipt | null>;
  /**
   * One transaction: fenced run CAS, workspace HEAD CAS, `journal` appends
   * (already validated and claim-checked), v1 events, transcript projection,
   * outbox and receipt.
   */
  commit(
    command: CommitBatch,
    journal: readonly PreparedJournalAppend[],
    transcript?: RunTranscript,
  ): Promise<CommitReceipt>;
  /**
   * `journal`: the v2 run's Node events, appended in the same transaction;
   * `transcript`: written there too when the control ends the run.
   */
  control(
    command: ControlRun,
    journal: readonly PreparedJournalAppend[],
    transcript?: RunTranscript,
    fence?: CommitFence,
  ): Promise<CommitReceipt>;
  /**
   * Fails a non-terminal run for a store-side reason; false if it already ended, or if an attempt other than `holder`
   * still holds it on an unexpired lease (`heldByAnotherAttempt`), judged on the store's clock in the same
   * compare-and-set. `holder`: the attempt the caller acts for, which may fail the run it holds.
   */
  failRun(
    orgId: string,
    runId: string,
    errorType: string,
    journal: readonly PreparedJournalAppend[],
    holder?: string,
  ): Promise<boolean>;
  get(orgId: string, runId: string): Promise<HarnessRun | null>;
  /** The session's completed children of root runs, latest first (the engine state a code_task child carries). */
  completedRootChildren(
    orgId: string,
    sessionId: string,
    limit: number,
  ): Promise<HarnessRun[]>;
  /** WP-64: every run of the session with the run that spawned it, for the run tree a claim names skips across. */
  sessionRunParents(orgId: string, sessionId: string): Promise<RunParent[]>;
  getActive(orgId: string, sessionId: string): Promise<HarnessRun | null>;
  /**
   * The org's runs a decision can still reach (`DECIDABLE_RUN_STATUSES`) with
   * an interaction nobody resolved yet, newest first (P2.11e's queue).
   */
  pendingApprovalRuns(
    orgId: string,
    filter: PendingApprovalFilter,
  ): Promise<HarnessRun[]>;
  /**
   * G-13: the actor's root runs that completed asking them a question
   * (`needs_input`) and are still their session's latest run, so nobody has
   * answered yet; newest first.
   */
  unansweredQuestionRuns(
    orgId: string,
    filter: UnansweredQuestionFilter,
  ): Promise<HarnessRun[]>;
  readEvents(
    orgId: string,
    sessionId: string,
    after: number,
    limit: number,
  ): Promise<HarnessEvent[]>;
  /**
   * One bounded page of expiry handling: worker-owned runs whose lease lapsed
   * are re-queued (or cancelled / failed), and idle runs past their absolute
   * deadline are failed, releasing the session. Returns runs transitioned.
   * A run failed at its deadline gets its `transcripts` projection written in
   * the transaction that journals the failure.
   */
  sweep(limit: number, transcripts?: SweptTranscripts): Promise<number>;
  /**
   * Interactive root runs (no schedule trigger, no parent) still queued with
   * no attempt counted, claimable and unchanged for `ageMs` (DB time), oldest
   * first (UX3). The age counts from the later of the last change and
   * `notBefore`: a run a worker yielded until later is waiting, not unclaimed,
   * until that time has passed. A run waiting for a worker feature is DUR2's
   * to release, not this.
   */
  unclaimedRuns(ageMs: number, limit: number): Promise<HarnessRun[]>;
  /**
   * Inline runs still queued `tClaimMs` after they became claimable become
   * dispatched, with a `run_ready` wake hint. Returns runs promoted.
   */
  promoteInline(tClaimMs: number, limit: number): Promise<number>;
  /**
   * The stored labels of the runs `run`'s model saw in full (P2.5a,
   * `seenRuns`): the latest earlier root run it saw and its visible runs. A
   * run whose label nobody stored is `null` (unknown, never empty).
   */
  historyLabels(run: HarnessRun): Promise<Array<TurnLabel | null>>;
  /**
   * The run once it ended, with its stored turn label, for work its ending
   * owes that may have been lost with the process (memory settlement); null
   * while it runs, or when the org has no such run.
   */
  readEnding(orgId: string, runId: string): Promise<RunEnding | null>;
  /**
   * Stores a completed run's suggested next questions (UX5) unless it has
   * some already: a retry reads back what the first write stored. Leaves
   * `revision` alone, so no fenced CAS on the run notices. Null: the org has
   * no such run.
   */
  storeFollowups(
    orgId: string,
    runId: string,
    followups: readonly Followup[],
  ): Promise<FollowupsWrite | null>;
}
