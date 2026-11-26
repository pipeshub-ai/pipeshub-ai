/** The appended events a recorder writes for: where they went and the seq of the first. */
export interface AppendedEvents {
  orgId: string;
  sessionId: string;
  runId?: string;
  streamId: string;
  /** The journal seq of the batch's first event; the event at index `i` has `firstSeq + i`. */
  firstSeq: number;
  /** The append's store time, in ms. */
  at: number;
}

/**
 * Keeps something of each journal append's events inside the append's own
 * transaction: an extension point (`HarnessEdition.journalRecorders`), none
 * by default. `take` reads one event's plain data before it is sealed or
 * claim-checked; `record` writes what was taken, keyed by the events' seqs,
 * so a record exists exactly when its event does and is never made twice.
 */
export interface JournalAppendRecorder<Tx> {
  /** The key a claim-checked event keeps this recorder's take under (`PreparedJournalEvent.taken`). */
  readonly name: string;
  /** What this recorder keeps of one event; null: nothing. */
  take(type: string, data: Readonly<Record<string, unknown>>): unknown;
  /** Writes what was taken (`taken[i]` for the batch's event `i`; null where nothing was), in `tx`. */
  record(
    tx: Tx,
    appended: AppendedEvents,
    taken: readonly unknown[],
  ): Promise<void>;
}
