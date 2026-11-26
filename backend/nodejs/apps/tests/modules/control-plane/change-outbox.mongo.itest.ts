/**
 * CPD-6 on a MongoDB replica set: `MongoChangeOutbox` under the transaction
 * contract (TX) on both transaction adapters, and the path a change event
 * takes from a committed write to a subscriber: the shared outbox row, the
 * application's `OutboxDispatcher`, the broker (NTF-01..03, CPD6-30). Run as
 * execution-store.mongo.itest.ts says.
 */
import { CpOrgHeadModel } from '../../../src/libs/repositories/control-plane/org-heads/schema/org-heads.schema';
import 'reflect-metadata';
import { expect } from 'chai';
import { ClientSession, Model } from 'mongoose';
import { OutboxDispatcher } from '../../../src/libs/services/outbox/outbox.dispatcher';
import { OutboxEvent } from '../../../src/libs/repositories/outbox/schema/outbox.schema';
import {
  BrokerChangeNotifier,
  ChangeEvent,
  CONTROL_PLANE_EVENTS_TOPIC,
  CpTx,
  MongoChangeOutbox,
  MongoControlPlaneTransaction,
  MongoOrgHeadLeases,
  OrderedStepsTransaction,
} from '../../../src/modules/control-plane';
import { MongoAuditChainStore } from '../../../src/libs/repositories/agent-harness/audit/mongo';
import { DURABLE_TRANSACTION_OPTIONS } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { ChainConfigAuditLog } from '../../../src/modules/agent-harness/adapters/chain-config-audit-log';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
  resetHarnessMongo,
} from '../agent-harness/support/harness-mongo';
import { createMockLogger } from '../../helpers/mock-logger';
import { RecordingLog } from './support/in-memory';
import { InMemoryBroker } from './support/in-memory-broker';
import { aChange, CasRegister, ORG, OTHER_ORG, setRegister, transactionContract } from './support/transaction-contract';

registerHarnessModels([CpOrgHeadModel, OutboxEvent] as unknown as Array<Model<never>>);

const rows = () => OutboxEvent.find({ topic: CONTROL_PLANE_EVENTS_TOPIC }).sort({ createdAt: 1, _id: 1 }).lean();
const events = async (): Promise<ChangeEvent[]> => (await rows()).map((row) => (JSON.parse(row.value) as { payload: ChangeEvent }).payload);
const configRecords = async (orgId: string) =>
  (await new MongoAuditChainStore().read(orgId, 0, 1_000)).filter((record) => record.streamId === `cp:${orgId}`);
const headOf = async (orgId: string) => (await CpOrgHeadModel.findById(orgId).lean())?.revision ?? 0;
const ctx = (orgId = ORG) => ({ orgId, actor: 'user:u-admin' });

function world() {
  const announced: ChangeEvent[] = [];
  const outbox = new MongoChangeOutbox({ announce: (event) => void announced.push(event) });
  const log = new RecordingLog();
  const transaction = new MongoControlPlaneTransaction({
    audit: new ChainConfigAuditLog(new MongoAuditChainStore()),
    outbox,
    log,
    transaction: DURABLE_TRANSACTION_OPTIONS,
  });
  return { announced, outbox, log, transaction };
}

describe('CPD-6 change events through the shared outbox on a MongoDB replica set', function () {
  this.timeout(120_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(async () => {
    await resetHarnessMongo();
    await OutboxEvent.deleteMany({});
  });

  transactionContract('mongo (replica set) with MongoChangeOutbox', {
    atomic: true,
    make: async (options = {}) => {
      const log = new RecordingLog();
      return {
        transaction: new MongoControlPlaneTransaction({
          audit: options.audit ?? new ChainConfigAuditLog(new MongoAuditChainStore()),
          outbox: new MongoChangeOutbox({ announce: () => undefined }),
          log,
          transaction: DURABLE_TRANSACTION_OPTIONS,
        }),
        events,
        log,
        head: headOf,
        configRecords,
        failCommit: async (tx: CpTx) => {
          await (tx.session as ClientSession).abortTransaction();
        },
      };
    },
  });

  transactionContract('ordered steps (cpOrgHeads) with MongoChangeOutbox', {
    atomic: false,
    make: async (options = {}) => {
      const chained = new ChainConfigAuditLog(new MongoAuditChainStore());
      const audit =
        options.audit === undefined
          ? chained
          : { record: options.audit.record.bind(options.audit), recorded: chained.recorded.bind(chained) };
      const log = new RecordingLog();
      return {
        transaction: new OrderedStepsTransaction({
          audit,
          heads: new MongoOrgHeadLeases(),
          outbox: new MongoChangeOutbox({ announce: () => undefined }),
          log,
          leaseMs: 5_000,
        }),
        events,
        log,
        head: headOf,
        configRecords,
      };
    },
  });

  it('NTF-01, NTF-13: a committed write leaves one pending row that is the event, keyed and ordered by its org, and announces it after the commit', async () => {
    const w = world();
    let announcedInside = -1;
    let rowsInside = -1;

    const committed = await w.transaction.run(ctx(), async (tx) => {
      const planned = await setRegister(new CasRegister(), 'a')(tx);
      announcedInside = w.announced.length;
      rowsInside = (await rows()).length;
      return planned;
    });

    const event: ChangeEvent = {
      type: 'cp.changed',
      orgId: ORG,
      revision: committed.headRevision,
      previous: committed.headRevision - 1,
      touched: [{ kind: 'platform-settings', id: 'labs' }],
      narrowing: false,
    };
    expect([announcedInside, rowsInside], 'inside the transaction: not announced, not visible outside it').to.deep.equal([0, 0]);
    expect(w.announced).to.deep.equal([event]);
    const [row] = await rows();
    expect([row?.topic, row?.key, row?.orderingKey, row?.status, row?.attempts]).to.deep.equal([
      'control-plane-events',
      ORG,
      `cp:${ORG}`,
      'pending',
      0,
    ]);
    expect(JSON.parse(row?.value ?? 'null')).to.deep.equal({ eventType: 'cp.changed', payload: event });
  });

  it('NTF-02: a transaction that aborts leaves no row and announces nothing', async () => {
    const w = world();
    let failed: unknown;
    await w.transaction
      .run(ctx(), async (tx) => {
        const planned = await setRegister(new CasRegister(), 'a')(tx);
        return {
          ...planned,
          result: tx.external(async () => {
            await (tx.session as ClientSession).abortTransaction();
          }),
        };
      })
      .catch((error: unknown) => {
        failed = error;
      });

    expect(failed).to.be.instanceOf(Error);
    expect(await rows()).to.deep.equal([]);
    expect(w.announced).to.deep.equal([]);
  });

  it('ordered steps: the row is written when the change is recorded, and announced once', async () => {
    const announced: ChangeEvent[] = [];
    const transaction = new OrderedStepsTransaction({
      audit: new ChainConfigAuditLog(new MongoAuditChainStore()),
      heads: new MongoOrgHeadLeases(),
      outbox: new MongoChangeOutbox({ announce: (event) => void announced.push(event) }),
      leaseMs: 5_000,
    });

    const committed = await transaction.run(ctx(), setRegister(new CasRegister(), 'a'));

    expect(announced.map((event) => event.revision)).to.deep.equal([committed.headRevision]);
    expect(await events()).to.deep.equal(announced);
  });

  it("NTF-03, CPD6-30: with the broker down the writes commit and their rows wait; the dispatcher's next pass delivers each org's events in revision order", async () => {
    const broker = new InMemoryBroker();
    const w = world();
    const dispatcher = new OutboxDispatcher(broker.producer(), createMockLogger() as never);
    const notifier = new BrokerChangeNotifier({ producer: broker.producer(), consumer: broker.consumer('worker-process') });
    const got: string[] = [];
    notifier.subscribe((event) => got.push(`${event.orgId}@${String(event.revision)}<${String(event.previous)}`));
    await notifier.listen();
    const register = new CasRegister();

    broker.down = true;
    await w.transaction.run(ctx(), setRegister(register, 'a'));
    await w.transaction.run(ctx(), setRegister(register, 'b', aChange('approvals')));
    expect(await dispatcher.drain(), 'nothing is delivered while the broker is down').to.equal(0);
    expect((await rows()).map((row) => [row.status, row.attempts])).to.deep.equal([
      ['pending', 1],
      ['pending', 0],
    ]);
    expect(await headOf(ORG), 'the writes committed').to.equal(2);

    broker.down = false;
    await w.transaction.run(ctx(OTHER_ORG), setRegister(new CasRegister(), 'x'));
    await w.transaction.run(ctx(), setRegister(register, 'c', aChange('model-access')));
    // The first row is in backoff after its failed attempt: until it is due, nothing later of its org may pass it.
    await dispatcher.drain();
    await broker.settled();
    expect(got, 'only the other org, whose rows nothing blocks').to.deep.equal([`${OTHER_ORG}@1<0`]);

    await OutboxEvent.updateMany({ status: 'pending' }, { $set: { nextAttemptAt: new Date(0) } });
    await dispatcher.drain();
    await broker.settled();
    expect(got).to.deep.equal([`${OTHER_ORG}@1<0`, `${ORG}@1<0`, `${ORG}@2<1`, `${ORG}@3<2`]);
    expect((await rows()).every((row) => row.status === 'published')).to.equal(true);
  });
});
