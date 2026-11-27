/**
 * LAT1 NT-2, NT-6, NT-9 on a MongoDB replica set: a transaction that makes a
 * run claimable announces it only after the commit, so a claimer woken by the
 * notice always finds the run; an aborted transaction announces nothing.
 * Run like execution-store.mongo.itest.ts.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { InMemoryRunReadyNotifier } from '../../../src/modules/agent-harness/adapters/in-memory-run-ready';
import { inMongoTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { MongoDispatchOutbox } from '../../../src/libs/repositories/agent-harness/dispatch-outbox/mongo';
import { OutboxEvent } from '../../../src/libs/repositories/outbox/schema/outbox.schema';
import { contractClock, createContractHarness } from './support/execution-store-contract';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  mongoBackend,
  resetHarnessMongo,
} from './support/harness-mongo';

describe('LAT1: run-ready after commit on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  it('NT-2, NT-6: a dispatched admission is announced after its commit, when its outbox row is already visible', async () => {
    const ready = new InMemoryRunReadyNotifier();
    const visibleAtNotice: Array<Promise<number>> = [];
    ready.listen(() => {
      visibleAtNotice.push(OutboxEvent.countDocuments({}));
    });
    const h = createContractHarness(mongoBackend({ dispatches: new MongoDispatchOutbox(ready) }), contractClock());
    const admitted = await h.admit();
    expect(ready.notices).to.deep.equal([{ orgId: admitted.run.orgId, runId: admitted.run.runId, kind: 'run_ready' }]);
    expect(await Promise.all(visibleAtNotice)).to.deep.equal([1]);
    await h.admit({ idempotencyKey: 'replayed' });
    await h.admit({ idempotencyKey: 'replayed' });
    expect(ready.notices, 'a replayed admission announces nothing new').to.have.length(2);
  });

  it('NT-9: every record of one transaction, in order (a resume and a child run_ready)', async () => {
    const ready = new InMemoryRunReadyNotifier();
    const outbox = new MongoDispatchOutbox(ready);
    await inMongoTransaction(async (session) => {
      await outbox.enqueue([{ orgId: 'o', runId: 'parent', commandId: 'c1', kind: 'run_resume', createdAt: 1 }], session);
      await outbox.enqueue([{ orgId: 'o', runId: 'child', commandId: 'c2', kind: 'run_ready', createdAt: 1 }], session);
      expect(ready.notices, 'nothing inside the transaction').to.deep.equal([]);
    });
    expect(ready.notices.map((n) => [n.runId, n.kind])).to.deep.equal([
      ['parent', 'run_resume'],
      ['child', 'run_ready'],
    ]);
  });

  it('NT-2: an aborted transaction announces nothing and leaves no row', async () => {
    const ready = new InMemoryRunReadyNotifier();
    const outbox = new MongoDispatchOutbox(ready);
    await inMongoTransaction(async (session) => {
      await outbox.enqueue([{ orgId: 'o', runId: 'r', commandId: 'c', kind: 'run_ready', createdAt: 1 }], session);
      throw new Error('a later write failed');
    }).catch(() => undefined);
    expect(ready.notices).to.deep.equal([]);
    expect(await OutboxEvent.countDocuments({})).to.equal(0);
  });
});
