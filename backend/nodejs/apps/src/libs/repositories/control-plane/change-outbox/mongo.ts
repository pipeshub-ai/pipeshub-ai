import type { ClientSession } from 'mongoose';
import { OutboxEvent } from '../../outbox/schema/outbox.schema';
import { AfterCommitPublisher } from '../../../utils/after-commit';
import type { ChangeEvent } from '../../../../modules/control-plane/contracts/generated';
import { changeEventMessage } from '../../../../modules/control-plane/domain/change-message';
import type { ChangeOutbox } from '../../../../modules/control-plane/ports/config-audit-log';
import type { CpTx } from '../../../../modules/control-plane/ports/transaction';

export interface MongoChangeOutboxOptions {
  /** Called after the commit with each event of the write, in order. */
  announce: (event: ChangeEvent) => void;
  commits?: AfterCommitPublisher<CpTx>;
  now?: () => Date;
}

/**
 * Change events in the shared outbox the application's `OutboxDispatcher`
 * delivers (the one outbox, as run dispatches use it): the row is written with
 * the write's session, so it exists exactly when the write committed, and the
 * dispatcher publishes an org's rows in the order written. Without a session
 * (standalone Mongo's ordered steps) the row is written when the change is
 * recorded.
 */
export class MongoChangeOutbox implements ChangeOutbox {
  private readonly commits: AfterCommitPublisher<CpTx>;
  private readonly now: () => Date;

  constructor(private readonly options: MongoChangeOutboxOptions) {
    this.commits = options.commits ?? new AfterCommitPublisher<CpTx>();
    this.now = options.now ?? (() => new Date());
  }

  async enqueue(tx: CpTx, event: ChangeEvent): Promise<void> {
    this.commits.defer(tx, () => {
      this.options.announce(event);
      return Promise.resolve();
    });
    const session = tx.session as ClientSession | undefined;
    await tx.enlist(async () => {
      await OutboxEvent.create(
        [
          {
            ...changeEventMessage(event),
            status: 'pending' as const,
            attempts: 0,
            nextAttemptAt: this.now(),
          },
        ],
        session !== undefined ? { session } : {},
      );
    });
  }

  committed(tx: CpTx): Promise<void> {
    return this.commits.committed(tx);
  }
}
