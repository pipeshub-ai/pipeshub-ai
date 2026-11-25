import { HarnessRun } from '../domain/execution';
import {
  ErasureCounts,
  ErasureJob,
  ErasureRequest,
  ErasureStep,
  ErasureStepOutcome,
} from '../domain/session-erasure';

/** Enqueues erasures; joins the caller's transaction (the delete's), when given one. */
export interface SessionErasureQueue<Tx> {
  /** True when this call created the job; false when the session already had one. */
  enqueue(request: ErasureRequest, tx?: Tx): Promise<boolean>;
  /** `enqueue` of every request, all of one org, in one store write; how many jobs this call created. */
  enqueueMany(requests: readonly ErasureRequest[], tx?: Tx): Promise<number>;
  /** The session's erasure, pending or completed; null when it was never erased. */
  find(orgId: string, sessionId: string): Promise<ErasureJob | null>;
}

/**
 * The durable jobs. A claim bumps the job's `attempt` under a lease on store
 * time; every later write names that attempt, so a holder that lost the job
 * can no longer move it.
 */
export interface ErasureJobStore<Tx> extends SessionErasureQueue<Tx> {
  /** One due pending job, oldest first, now held by `holder`; null when none is due. */
  claimDue(holder: string, leaseMs: number): Promise<ErasureJob | null>;
  /**
   * Marks `job.step` done with `counts` and moves to `next` (null: completed).
   * False when the job is no longer at this attempt and step.
   */
  advance(
    job: ErasureJob,
    counts: ErasureCounts,
    next: ErasureStep | null,
    leaseMs: number,
    tx?: Tx,
  ): Promise<boolean>;
  /**
   * Removes a pending job at this attempt and step: its conversation is live
   * again, so nothing of it may be erased. False when the job moved on.
   */
  withdraw(job: ErasureJob): Promise<boolean>;
  /** The jobs of these sessions of one org that exist, keyed by session id. */
  findMany(
    orgId: string,
    sessionIds: readonly string[],
  ): Promise<Map<string, ErasureJob>>;
  /** Due again at `at`; a failure counts towards backoff. False when the job moved on. */
  reschedule(
    job: ErasureJob,
    at: number,
    failure: { errorType: string } | null,
  ): Promise<boolean>;
  /** The store's clock (DB time for Mongo). */
  now(): Promise<number>;
}

/** The session's runs as erasure reads them. */
export type ErasureSessionRuns = Awaited<
  ReturnType<SessionRunLookup['runsOf']>
>;

/**
 * What the steps of one pass of the runner (one claim, steps in order until it waits or ends) share: once the runs
 * have ended no step adds one, so the steps after `await_runs_terminal` reuse its read instead of each reading the
 * session's runs again. A new pass starts empty.
 */
export interface ErasurePass {
  runs?: ErasureSessionRuns;
}

/** What a step may do beyond its own store: complete itself inside its own transaction. */
export interface ErasureStepContext<Tx> {
  /** Marks this step done in `tx`; false (abort the transaction) when another owner has the job. */
  completeIn(tx: Tx, counts: ErasureCounts): Promise<boolean>;
  /** Absent: the step reads what it needs itself. */
  pass?: ErasurePass;
}

/**
 * One erasure step over the stores it owns. `erase` must be idempotent: it
 * runs again, from the start, after a crash anywhere in it.
 */
export interface SessionDataEraser<Tx> {
  readonly step: ErasureStep;
  erase(
    job: ErasureJob,
    context: ErasureStepContext<Tx>,
  ): Promise<ErasureStepOutcome | { kind: 'completed_in_transaction' }>;
}

/** The session's runs as erasure sees them. */
export interface SessionRunLookup {
  runsOf(
    orgId: string,
    sessionId: string,
  ): Promise<
    Array<Pick<HarnessRun, 'runId' | 'status' | 'revision' | 'harnessMajor'>>
  >;
}

/** The harness stores keyed by the session (Mongo in production). */
export interface SessionStoreEraser {
  /**
   * Manifests, heads and write leases of `ws:{sessionId}` and every
   * `ws:{sessionId}:*` workspace (forks included).
   */
  eraseWorkspaces(
    orgId: string,
    sessionId: string,
  ): Promise<{ manifests: number; heads: number; writers?: number }>;
  /**
   * Payloads the session owns, and unowned (pre-E7) payloads its events
   * point at whose writer is one of `runIds`; never another session's.
   */
  erasePayloads(
    orgId: string,
    sessionId: string,
    runIds: readonly string[],
  ): Promise<{ payloads: number }>;
  /**
   * Event content reduced to tombstones, v1 events, the runs' command
   * receipts, the session's admission receipts, the fork requests naming the
   * session (SS3, `forkRecords` where the store keeps them), the runs' inputs,
   * and a caller's idempotency key an older build left on a run (`runKeys`).
   */
  tombstoneJournal(
    orgId: string,
    sessionId: string,
    runIds: readonly string[],
  ): Promise<{
    events: number;
    v1Events: number;
    receipts: number;
    admissionReceipts: number;
    forkRecords?: number;
    runInputs: number;
    runKeys?: number;
  }>;
}

/** A conversation as erasure must see it before each step: still deleted, gone, or live. */
export type ConversationState = 'deleted' | 'absent' | 'live';

/**
 * Read immediately before every erasure step (E7B): a job whose conversation
 * is live (restored, or never deleted) erases nothing more.
 */
export interface ConversationStateLookup {
  state(orgId: string, sessionId: string): Promise<ConversationState>;
}

/** The conversation's own documents, owned by the conversation module. */
/**
 * What the `conversation` step erased; `attachmentsRefused` only when the query
 * service refused some, `attachmentsUnrecorded` (of those, uploads older than
 * the uploader record that the sender holds no OWNER edge to) and
 * `attachmentsMarkedRemoved` (messages of other conversations naming an erased
 * upload) only when non-zero.
 */
export type ConversationErasure = {
  citations: number;
  messages: number;
  sessions: number;
  attachments: number;
  attachmentBlobs: number;
  attachmentsRefused?: number;
  attachmentsUnrecorded?: number;
  attachmentsMarkedRemoved?: number;
};

/**
 * A conversation's own documents and the chat attachments its messages carry
 * (QA15-F4), the attachments first: a failure leaves the messages that name
 * them for the rerun.
 */
export interface ConversationEraser {
  erase(orgId: string, sessionId: string): Promise<ConversationErasure>;
}

/** A chat attachment a message carries, with that message's sender (the only user it may be erased for). */
export interface ConversationAttachment {
  recordId: string;
  ownerId: string;
}

/** What one call of the query service's attachment erasure did. */
export interface AttachmentErasurePage {
  attachments: number;
  blobs: number;
  refused: number;
  /** Of the refused, uploads that predate the uploader record and that the sender holds no OWNER edge to. */
  unrecorded: number;
  /** The named record ids no record holds any more (erased now or before); never a refused one. */
  removed: readonly string[];
}

/** The uploads behind chat attachments (the query service): records, files, edges and stored bytes. */
export interface AttachmentEraser {
  /** At most `ATTACHMENT_ERASE_PAGE` per call; one gone already counts nothing. */
  eraseAttachments(
    orgId: string,
    attachments: readonly ConversationAttachment[],
  ): Promise<AttachmentErasurePage>;
}

/** A chat attachment uploaded and not yet confirmed carried by a message (CLEANUP-G5). */
export interface UnsentAttachment {
  recordId: string;
  ownerId: string;
  conversationId: string | null;
}

/**
 * The query service's uploads still marked as waiting for a message
 * (CLEANUP-G5). Only Node knows the messages, so it decides: an upload a
 * message carries is marked sent; one none carries is erased.
 */
export interface UnsentAttachments {
  /**
   * The org's waiting uploads made before `before`, at most `limit`, from the
   * `after` cursor; `next` is where the next page starts (a page may hold
   * none while more remain, since the age is not what the store pages by).
   */
  unsent(
    orgId: string,
    before: number,
    limit: number,
    after: string | null,
  ): Promise<{
    uploads: UnsentAttachment[];
    remaining: boolean;
    next: string | null;
  }>;
  markSent(
    orgId: string,
    conversationId: string,
    recordIds: readonly string[],
  ): Promise<void>;
}

/** Which conversation's message carries each upload (CLEANUP-G5); an id no message carries is absent. */
export interface AttachmentCarriers {
  /** Orgs whose uploads the collector looks at. */
  orgs(): Promise<string[]>;
  carriedBy(
    orgId: string,
    recordIds: readonly string[],
  ): Promise<Map<string, string>>;
}

/** The artifact registry's records and blobs of one conversation (the query service). */
export interface ArtifactEraser {
  eraseConversation(
    orgId: string,
    sessionId: string,
  ): Promise<{ artifacts: number; blobs: number; remaining: boolean }>;
  /** Drafts a cancelled task discarded before `before` (P3.5), across the org. */
  collectDiscarded(
    orgId: string,
    before: number,
  ): Promise<{ artifacts: number; blobs: number; remaining: boolean }>;
}

/**
 * The session's traces in Opik (the query service, which holds the Opik
 * client): one call per page of at most `TRACE_ERASE_PAGE` run ids, and
 * one with none for the conversation's thread. Rejects when Opik could not
 * be asked, so the step is retried; resolves with nothing to do when this
 * deployment sends no traces.
 */
export interface TraceEraser {
  eraseTraces(
    orgId: string,
    sessionId: string,
    runIds: readonly string[],
  ): Promise<{ traces: number }>;
}

/** Deletes the LiveBus streams of runs. */
export interface LiveBusEraser {
  erase(runIds: readonly string[]): Promise<number>;
}
