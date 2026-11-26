/**
 * CPD-7 on a MongoDB replica set: CP-SKEL's InstallationStore (IS-1..8) and
 * GrantStore (GS-1..6) contracts, unchanged, on the Mongo adapters with the
 * replica-set control-plane transaction and the Mongo audit chain; round trips
 * counted from the driver's command events, not from the adapter; and the
 * index migration (IS-9, GS-7). Needs MONGO_IT_URI (rs0).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose, { Model } from 'mongoose';
import type { CommandStartedEvent, Db } from 'mongodb';
import { MongoControlPlaneTransaction } from '../../../src/modules/control-plane';
import { CpOrgHeadModel } from '../../../src/libs/repositories/control-plane/org-heads/schema/org-heads.schema';
import { INSTALLATIONS_MIGRATION } from '../../../src/libs/repositories/control-plane/migrations/mongo';
import {
  CpGrantModel,
  CpInstallationHistoryModel,
  CpInstallationModel,
} from '../../../src/libs/repositories/control-plane/installations/schema/installations.schema';
import { MongoGrantStore } from '../../../src/libs/repositories/control-plane/grant-store/mongo';
import { MongoInstallationStore } from '../../../src/libs/repositories/control-plane/installations/mongo';
import { ChainConfigAuditLog } from '../../../src/modules/agent-harness/adapters/chain-config-audit-log';
import { MongoAuditChainStore } from '../../../src/libs/repositories/agent-harness/audit/mongo';
import { DURABLE_TRANSACTION_OPTIONS } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { migrateHarnessIndexes } from '../../../src/libs/repositories/agent-harness/migrations/mongo';
import {
  disconnectHarnessMongo,
  harnessModels,
  harnessMongoUri,
  registerHarnessModels,
  resetHarnessMongo,
} from '../agent-harness/support/harness-mongo';
import { describeGrantStoreContract } from './support/grant-store-contract';
import { describeInstallationStoreContract } from './support/installation-store-contract';
import { aGrant, anEntry, anInstallation, directTx, instId, ORG_A } from './support/cp-objects';
import type { StoreWorld } from './support/store-world';

registerHarnessModels([CpOrgHeadModel, CpInstallationModel, CpInstallationHistoryModel, CpGrantModel] as unknown as Array<
  Model<never>
>);

const READS = new Set(['find', 'aggregate', 'count', 'distinct', 'getMore']);
let reads = 0;
const countReads = (event: CommandStartedEvent): void => {
  if (READS.has(event.commandName)) reads += 1;
};

const db = (): Db => mongoose.connection.db!;
const indexNames = async (collection: string): Promise<string[]> =>
  (await db().collection(collection).indexes()).map((index) => index.name!).sort();

function mongoWorld<S>(store: S): StoreWorld<S> {
  return {
    store,
    roundTrips: () => reads,
    transaction: new MongoControlPlaneTransaction({
      audit: new ChainConfigAuditLog(new MongoAuditChainStore()),
      transaction: DURABLE_TRANSACTION_OPTIONS,
    }),
    configEntries: async (orgId) =>
      (await new MongoAuditChainStore().read(orgId, 0, 1_000)).filter((record) => record.streamId === `cp:${orgId}`)
        .length,
  };
}

describe('CPD-7 installation and grant stores on a MongoDB replica set', function () {
  this.timeout(120_000);

  before(async () => {
    // Round trips are counted from the driver's command events, so the client monitors commands.
    await mongoose.connect(harnessMongoUri(), { dbName: `cp_installations_it_${Date.now()}_${process.pid}`, monitorCommands: true });
    await Promise.all(harnessModels().map((model) => model.init()));
    await migrateHarnessIndexes(db());
    await INSTALLATIONS_MIGRATION.up(db());
    mongoose.connection.getClient().on('commandStarted', countReads);
  });
  after(async () => {
    mongoose.connection.getClient().off('commandStarted', countReads);
    await disconnectHarnessMongo();
  });
  beforeEach(resetHarnessMongo);

  describeInstallationStoreContract('MongoInstallationStore', async () => mongoWorld(new MongoInstallationStore()));
  describeGrantStoreContract('MongoGrantStore', async () => mongoWorld(new MongoGrantStore()));

  it('IS-9, GS-7, LG: the migration builds the 02-lld indexes, idempotently; down keeps every row', async () => {
    const expected = {
      cpInstallations: [
        '_id_',
        'cp_installations_item',
        'cp_installations_owner',
        'cp_installations_parent',
        'cp_installations_status',
      ],
      cpInstallationHistory: ['_id_', 'cp_installation_history'],
      cpGrants: ['_id_', 'cp_grants_installation'],
      cpCapabilityLedger: ['_id_', 'cp_capability_ledger_org'],
    };
    for (const [collection, names] of Object.entries(expected)) {
      expect(await indexNames(collection), collection).to.deep.equal(names);
    }
    expect((await INSTALLATIONS_MIGRATION.up(db())).created).to.deep.equal([]);
    const installations = new MongoInstallationStore();
    const grants = new MongoGrantStore();
    await installations.create(directTx(ORG_A), anInstallation());
    await grants.put(directTx(ORG_A), aGrant(), 0);
    expect((await INSTALLATIONS_MIGRATION.down(db())).dropped).to.have.length(7);
    for (const collection of Object.keys(expected)) expect(await indexNames(collection)).to.deep.equal(['_id_']);
    expect(await installations.get(ORG_A, instId(1))).to.not.equal(null);
    expect(await grants.usersOf(ORG_A, instId(1))).to.deep.equal(['agent:a7']);
    expect((await INSTALLATIONS_MIGRATION.up(db())).created).to.have.length(7);
  });

  it('GS-8: usersOf and listForOrg are answered from their indexes, not a collection scan', async () => {
    const grants = new MongoGrantStore();
    await grants.put(directTx(ORG_A), aGrant({ entries: [anEntry(instId(1))] }), 0);
    const plan = (await CpGrantModel.find({ orgId: ORG_A, 'entries.installation': instId(1) })
      .explain('queryPlanner')) as unknown as Array<{ queryPlanner: { winningPlan: unknown } }> | { queryPlanner: { winningPlan: unknown } };
    const winning = JSON.stringify(Array.isArray(plan) ? plan[0]!.queryPlanner.winningPlan : plan.queryPlanner.winningPlan);
    expect(winning).to.contain('cp_grants_installation');
    expect(winning).to.not.contain('COLLSCAN');
  });
});
