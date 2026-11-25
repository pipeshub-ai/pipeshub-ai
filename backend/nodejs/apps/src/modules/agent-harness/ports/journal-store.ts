import {
  JournalAppend,
  JournalAppendResult,
  JournalEvent,
  PreparedJournalAppend,
  validateJournalAppend,
} from '../domain/journal';

/**
 * Makes an append writable (`JournalService.prepare`): validated, and an
 * event over the inline bound claim-checked, before the store transaction.
 */
export interface JournalPreparer {
  /** `tx`: the transaction the append is written in, when it is prepared inside one (R3 M8). */
  prepare(append: JournalAppend, tx?: unknown): Promise<PreparedJournalAppend>;
}

/** Validation only, for a store that is given no payload store (tests). */
export const validatingPreparer: JournalPreparer = {
  prepare: (append) => {
    validateJournalAppend(append);
    return Promise.resolve(append);
  },
};

/**
 * The typed session journal (`sessionEvents`). Appends run inside the
 * caller's commit transaction and allocate a gap-free `seq` per stream there;
 * reads see only majority-committed events.
 */
export interface JournalStore<Tx> {
  /** Events must come from JournalService.prepare (validated, claim-checked). */
  append(batch: PreparedJournalAppend, tx: Tx): Promise<JournalAppendResult>;
  /** Highest seq per stream; 0 for a stream with no events. */
  lastSeq(
    orgId: string,
    streamIds: readonly string[],
    tx?: Tx,
  ): Promise<Record<string, number>>;
  readStream(
    orgId: string,
    streamId: string,
    afterSeq: number,
    limit: number,
  ): Promise<JournalEvent[]>;
  readRun(
    orgId: string,
    runId: string,
    afterSeq: number,
    limit: number,
  ): Promise<JournalEvent[]>;
}
