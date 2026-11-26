/**
 * Who wrote a stored payload: the session it belongs to and, when a run
 * wrote it, that run. Erasure deletes by session; GC asks whether the run
 * still runs (T5, I8).
 */
export interface PayloadOwner {
  sessionId: string;
  runId?: string;
}

/** Content-addressed store for claim-checked journal payloads, namespaced per org. */
export interface PayloadStore {
  /** Idempotent; returns the ref to record in `payloadRef.ref`. */
  put(
    orgId: string,
    hash: string,
    bytes: Buffer,
    owner?: PayloadOwner,
  ): Promise<string>;
  /** null when the object is absent (or belongs to another org). */
  get(orgId: string, ref: string): Promise<Buffer | null>;
}

/**
 * The references side of a payload store (T5): an append marks the payloads
 * its events point at in the append's own transaction, so a payload that no
 * committed event references is exactly one nothing can read.
 */
export interface PayloadReferences<Tx> {
  /** `refs` are store refs (`payloadRef.ref`, or the store part of a `sealed:` ref); another org's are ignored. */
  markReferenced(orgId: string, refs: readonly string[], tx: Tx): Promise<void>;
}

/**
 * A payload store that can write inside the caller's transaction (R3 M8). A
 * snapshot transaction does not see a payload written outside it after its
 * first read, so one it stores and cites in the same transaction must be
 * written in it: the append's reference mark then finds it, and an aborted
 * attempt leaves nothing behind.
 */
export interface TransactionalPayloadStore<Tx> {
  putIn(
    tx: Tx,
    orgId: string,
    hash: string,
    bytes: Buffer,
    owner?: PayloadOwner,
  ): Promise<string>;
}
