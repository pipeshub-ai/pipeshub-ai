/**
 * A condition a store write must still meet when it commits, checked inside
 * its transaction (QA21-F2). `hold` re-reads what the write depends on
 * through `session` (the store's database session; undefined in memory) and
 * writes to it, so a concurrent change to the same document and the write
 * cannot both commit; it throws when the condition no longer holds.
 */
export interface CommitFence {
  hold(session: unknown): Promise<void>;
}
