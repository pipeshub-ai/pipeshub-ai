import { asMongoSession } from '../durable-transaction/mongo';
import type { Tx } from '../../uow';
import { ClientSession } from 'mongoose';
import { OutboxEvent } from '../../outbox/schema/outbox.schema';
import { DispatchRecord } from '../../../../modules/agent-harness/domain/execution';
import { DispatchOutbox } from '../../../../modules/agent-harness/ports/dispatch-outbox';
import {
  noRunReady,
  RunReadyNotifier,
} from '../../../../modules/agent-harness/ports/run-ready';
import { dispatchMessage } from '../../../../modules/agent-harness/adapters/messaging/dispatch-envelope';
import { durableCommits } from '../durable-transaction/mongo';
import { createInTransaction } from '../transaction-writes/mongo';

/**
 * Writes dispatches to the shared outbox that OutboxDispatcher delivers, and
 * announces them to idle claimers once the transaction commits (LAT1); the
 * outbox stays the backstop for a lost announcement.
 */
export class MongoDispatchOutbox implements DispatchOutbox<Tx> {
  constructor(private readonly ready: RunReadyNotifier = noRunReady) {}

  async enqueue(
    records: DispatchRecord[],
    txHandle: Tx | ClientSession,
  ): Promise<void> {
    const tx = asMongoSession(txHandle);
    if (records.length === 0) return;
    const notices = records.map(({ orgId, runId, kind }) => ({
      orgId,
      runId,
      kind,
    }));
    durableCommits.defer(tx, () => this.ready.notify(notices));
    const now = new Date();
    const events = records.map((record) => ({
      ...dispatchMessage(record),
      status: 'pending' as const,
      attempts: 0,
      nextAttemptAt: now,
    }));
    await createInTransaction(tx, OutboxEvent, events);
  }
}
