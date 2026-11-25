import { DispatchRecord } from '../../../../modules/agent-harness/domain/execution';
import { DispatchOutbox } from '../../../../modules/agent-harness/ports/dispatch-outbox';
import {
  noRunReady,
  RunReadyNotifier,
} from '../../../../modules/agent-harness/ports/run-ready';
import { dispatchMessage } from '../../../../modules/agent-harness/adapters/messaging/dispatch-envelope';
import { PgOutboxRepository } from '../../outbox/pg';
import type { OutboxRepository } from '../../outbox/types';
import { kernelTx } from '../../pg';
import type { Tx } from '../../uow';
import { durableCommits } from '../durable-transaction/commits';
import { orgUnit } from '../pg-documents/pg';

/**
 * Writes dispatches to main's outbox (`platform.outbox`, delivered by its dispatcher as the jobs role) inside the
 * caller's unit, so a dispatch exists if and only if its state change does; idle claimers hear of them once the unit
 * has committed (LAT1), and the outbox stays the backstop for a lost announcement.
 */
export class PgDispatchOutbox implements DispatchOutbox<Tx> {
  constructor(
    private readonly ready: RunReadyNotifier = noRunReady,
    private readonly outbox: OutboxRepository = new PgOutboxRepository(),
  ) {}

  async enqueue(records: DispatchRecord[], tx: Tx): Promise<void> {
    if (records.length === 0) return;
    const notices = records.map(({ orgId, runId, kind }) => ({
      orgId,
      runId,
      kind,
    }));
    durableCommits.defer(kernelTx(tx), () => this.ready.notify(notices));
    const now = new Date();
    for (const record of records) {
      await orgUnit(tx, record.orgId, { write: true }, () =>
        this.outbox.append(
          {
            ...dispatchMessage(record),
            status: 'pending',
            attempts: 0,
            nextAttemptAt: now,
          },
          tx,
        ),
      );
    }
  }
}
