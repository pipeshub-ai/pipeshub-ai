/**
 * Unit of work: the only way repository code groups writes.
 *
 * Replaces `mongoose.startSession()` plus the `rsAvailable` branches in
 * controllers. A `Tx` is opaque to callers: it is handed to repository
 * methods (`create(x, tx)`) and never inspected, so no driver type crosses
 * the seam.
 */
export interface Tx {
  readonly store: 'mongo';
}

export interface UnitOfWork {
  /**
   * Runs `fn` and returns its result. Everything done with the given `tx`
   * commits together or not at all, when the store can do that (a Mongo replica
   * set). On a store that cannot (standalone Mongo, today's compose install)
   * the writes are applied one by one, exactly as before.
   *
   * `fn` may be called more than once if the store retries a transient
   * failure, so it must not have side effects outside `tx`.
   * Store errors leave as the typed errors in ./errors.
   */
  run<T>(fn: (tx: Tx) => Promise<T>): Promise<T>;
}
