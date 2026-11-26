/**
 * CPD-1 on a MongoDB replica set: the transaction contract (TX) on the
 * replica-set adapter and on ordered steps over `cpOrgHeads`, the audit
 * contract (CA) over MongoAuditChainStore, and the ordered-steps takeover
 * (OS-1) on the real lease document. Run as execution-store.mongo.itest.ts says.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose, { ClientSession, Model } from 'mongoose';
import { ChangeEvent, ChangeOutbox, ConfigAuditLog, ControlPlaneUnavailableError, CpTx, MongoControlPlaneTransaction, MongoOrgHeadLeases, OrderedStepsTransaction } from '../../../src/modules/control-plane';
import { CpOrgHeadModel } from '../../../src/libs/repositories/control-plane/org-heads/schema/org-heads.schema';
import { MongoAuditChainStore } from '../../../src/libs/repositories/agent-harness/audit/mongo';
import {
  DURABLE_TRANSACTION_OPTIONS,
  inMongoTransaction,
} from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { ChainConfigAuditLog } from '../../../src/modules/agent-harness/adapters/chain-config-audit-log';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
  resetHarnessMongo,
} from '../agent-harness/support/harness-mongo';
import { FailingConfigAuditLog, RecordingLog } from './support/in-memory';
import { aChange, CasRegister, ORG, setRegister, transactionContract } from './support/transaction-contract';
import { configAuditContract } from './support/config-audit-contract';

registerHarnessModels([CpOrgHeadModel] as unknown as Array<Model<never>>);

/** An outbox that writes through the transaction's session, as a Mongo outbox does. */
class SessionOutbox implements ChangeOutbox {
  private readonly collection = mongoose.connection.collection<ChangeEvent & { _id?: unknown }>('cpTestOutbox');

  enqueue(tx: CpTx, event: ChangeEvent): Promise<void> {
    return tx.enlist(async () => {
      await this.collection.insertOne({ ...event }, { session: tx.session as ClientSession });
    });
  }

  committed(): Promise<void> {
    return Promise.resolve();
  }

  async events(): Promise<ChangeEvent[]> {
    const rows = await this.collection.find({}, { projection: { _id: 0 } }).toArray();
    return rows as ChangeEvent[];
  }
}

const chainRecords = async (orgId: string) => new MongoAuditChainStore().read(orgId, 0, 1_000);
const configRecords = async (orgId: string) =>
  (await chainRecords(orgId)).filter((record) => record.streamId === `cp:${orgId}`);
const headOf = async (orgId: string) => (await CpOrgHeadModel.findById(orgId).lean())?.revision ?? 0;

const ordered = (options: { audit?: ConfigAuditLog; now?: () => number } = {}) => {
  const chained = new ChainConfigAuditLog(new MongoAuditChainStore());
  const audit =
    options.audit === undefined
      ? chained
      : { record: options.audit.record.bind(options.audit), recorded: chained.recorded.bind(chained) };
  const outbox = new SessionOutbox();
  const log = new RecordingLog();
  const transaction = new OrderedStepsTransaction({
    audit,
    heads: new MongoOrgHeadLeases(),
    outbox,
    log,
    leaseMs: 5_000,
    ...(options.now !== undefined ? { now: options.now } : {}),
  });
  return { transaction, outbox, log, chained };
};

describe('CPD-1 control-plane writes on a MongoDB replica set', function () {
  this.timeout(120_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(async () => {
    await resetHarnessMongo();
    await mongoose.connection.collection('cpTestOutbox').deleteMany({});
  });

  transactionContract('mongo (replica set)', {
    atomic: true,
    make: async (options = {}) => {
      const outbox = new SessionOutbox();
      const log = new RecordingLog();
      return {
        transaction: new MongoControlPlaneTransaction({
          audit: options.audit ?? new ChainConfigAuditLog(new MongoAuditChainStore()),
          outbox,
          log,
          transaction: DURABLE_TRANSACTION_OPTIONS,
        }),
        events: () => outbox.events(),
        log,
        head: headOf,
        configRecords,
        failCommit: async (tx: CpTx) => {
          await (tx.session as ClientSession).abortTransaction();
        },
      };
    },
  });

  transactionContract('ordered steps (cpOrgHeads)', {
    atomic: false,
    make: async (options = {}) => {
      const world = ordered(options);
      return { transaction: world.transaction, events: () => world.outbox.events(), log: world.log, head: headOf, configRecords };
    },
  });

  configAuditContract('chain over MongoAuditChainStore', async () => ({
    transaction: new MongoControlPlaneTransaction({
      audit: new ChainConfigAuditLog(new MongoAuditChainStore()),
      transaction: DURABLE_TRANSACTION_OPTIONS,
    }),
    chain: chainRecords,
    appendRun: async (orgId, entries) => {
      await inMongoTransaction((session) =>
        new MongoAuditChainStore().append(orgId, entries, new Date().toISOString(), session),
      );
    },
  }));

  it('OS-1: on the lease document, a takeover records the dead writer once and then commits its own write', async () => {
    let clock = Date.now();
    const failing = ordered({ audit: new FailingConfigAuditLog(), now: () => clock });
    const register = new CasRegister();
    expect(
      await failing.transaction.run({ orgId: ORG, actor: 'user:u-admin' }, setRegister(register, 'v1')).catch((e: unknown) => e),
    ).to.be.instanceOf(ControlPlaneUnavailableError);
    const stored = await CpOrgHeadModel.findById(ORG).lean();
    expect(stored?.lease?.intent?.headRevision).to.equal(1);

    clock += 1;
    const next = ordered({ now: () => clock });
    const committed = await next.transaction.run(
      { orgId: ORG, actor: 'user:u-admin' },
      setRegister(register, 'v2', aChange('second')),
    );
    expect(committed.headRevision).to.equal(2);
    expect((await configRecords(ORG)).map((record) => [record.journalSeq, record.fields['repaired']])).to.deep.equal([
      [1, true],
      [2, undefined],
    ]);
    expect(await CpOrgHeadModel.findById(ORG).lean()).to.deep.include({ revision: 2, lease: null });
    expect(await next.chained.recorded(ORG, 1)).to.equal(true);
    expect(await next.chained.recorded(ORG, 3)).to.equal(false);
  });

});
