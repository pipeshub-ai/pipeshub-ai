/**
 * Wakes a worker holding one of the run's calls for approval (P2.6c): a hint
 * after `resolveActive`, never the decision itself (the journal carries it,
 * and the holder re-reads it at least every poll interval). Best effort.
 * Adapters: Redis pub/sub on `[<namespace>:]harness:interaction-notify:<runId>`
 * (what `app/harness/interaction_wakeup.py` subscribes to) and in-memory.
 */
export interface RunWakeup {
  wake(runId: string): Promise<void>;
}

export const noRunWakeup: RunWakeup = { wake: () => Promise.resolve() };
