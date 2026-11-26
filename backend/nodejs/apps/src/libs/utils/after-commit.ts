export interface AfterCommitLog {
  warn(message: string, meta?: Record<string, unknown>): void;
}

const silent: AfterCommitLog = { warn: () => undefined };

type Publish = () => Promise<void>;

/**
 * Work a transaction defers until it has committed: notices that must never
 * announce a write that did not happen (control-plane 05 §5: run hand-off and
 * the control plane's change events share it). Per transaction handle: each attempt starts
 * empty, so a retried or aborted attempt publishes nothing; after the commit
 * the publishes run in the order they were deferred. A failed publish is
 * logged and never fails the commit: what it announced is already durable,
 * and the reader's poll finds it.
 */
export class AfterCommitPublisher<Tx extends object> {
  private readonly pending = new WeakMap<Tx, Publish[]>();

  constructor(private readonly logger: AfterCommitLog = silent) {}

  /** Called inside the transaction attempt `tx`. */
  defer(tx: Tx, publish: Publish): void {
    const queue = this.pending.get(tx);
    if (queue === undefined) this.pending.set(tx, [publish]);
    else queue.push(publish);
  }

  /** A transaction attempt starts: whatever an earlier attempt deferred is dropped. */
  begin(tx: Tx): void {
    this.pending.delete(tx);
  }

  /** The transaction did not commit. */
  discard(tx: Tx): void {
    this.pending.delete(tx);
  }

  /** The transaction committed: run its publishes, in order. */
  async committed(tx: Tx): Promise<void> {
    const queue = this.pending.get(tx) ?? [];
    this.pending.delete(tx);
    for (const publish of queue) {
      try {
        await publish();
      } catch (error) {
        this.logger.warn('after_commit.publish_failed', {
          event: 'after_commit.publish_failed',
          errorType: error instanceof Error ? error.name : typeof error,
          choice: 'poll_backstop',
        });
      }
    }
  }
}
