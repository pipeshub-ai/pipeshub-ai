import { AfterCommitPublisher } from '../../../utils/after-commit';
import type { ChangeEvent } from '../../../../modules/control-plane/contracts/generated';
import { changeEventMessage } from '../../../../modules/control-plane/domain/change-message';
import type { ChangeOutbox } from '../../../../modules/control-plane/ports/config-audit-log';
import type { CpTx } from '../../../../modules/control-plane/ports/transaction';
import { PgOutboxRepository } from '../../outbox/pg';
import type { OutboxRepository } from '../../outbox/types';
import { PgTx } from '../../pg';
import { cpUnit } from '../pg-units';

export interface PgChangeOutboxOptions {
  /** Called after the commit with each event of the write, in order. */
  announce: (event: ChangeEvent) => void;
  commits?: AfterCommitPublisher<CpTx>;
  outbox?: OutboxRepository;
  now?: () => Date;
}

/**
 * Change events in main's outbox (`platform.outbox`, delivered by its dispatcher as the jobs role), appended in the
 * write's unit: the row exists exactly when the write committed, and the dispatcher keeps an org's rows in order
 * (`orderingKey` `cp:{orgId}`). This process's holders hear the event once the unit has committed.
 */
export class PgChangeOutbox implements ChangeOutbox {
  private readonly commits: AfterCommitPublisher<CpTx>;
  private readonly outbox: OutboxRepository;
  private readonly now: () => Date;

  constructor(private readonly options: PgChangeOutboxOptions) {
    this.commits = options.commits ?? new AfterCommitPublisher<CpTx>();
    this.outbox = options.outbox ?? new PgOutboxRepository();
    this.now = options.now ?? (() => new Date());
  }

  async enqueue(tx: CpTx, event: ChangeEvent): Promise<void> {
    this.commits.defer(tx, () => {
      this.options.announce(event);
      return Promise.resolve();
    });
    await tx.enlist(() =>
      cpUnit(tx.session, tx.orgId, { write: true }, (k) =>
        this.outbox.append(
          {
            ...changeEventMessage(event),
            status: 'pending',
            attempts: 0,
            nextAttemptAt: this.now(),
          },
          new PgTx(k),
        ),
      ),
    );
  }

  committed(tx: CpTx): Promise<void> {
    return this.commits.committed(tx);
  }
}
