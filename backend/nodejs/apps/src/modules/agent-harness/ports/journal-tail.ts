export interface JournalTailSubscription {
  close(): void;
}

/**
 * Tells a stream that a run's journal may have grown, on whichever Node
 * replica committed it (REPORT 6.2.4 cross-replica tail). Carries no events:
 * the stream reads them, so a missed or duplicate signal costs latency only.
 * A signal for one committed event names its seq, so a stream that has it
 * already does not read; a signal without one (a safety poll) says only that
 * the journal may have grown.
 */
export interface JournalTail {
  watch(
    orgId: string,
    runId: string,
    onChange: (seq?: number) => void,
  ): JournalTailSubscription;
}
