/**
 * MG-9 (CPD-7): the capability migration and the ledger contract on a
 * MongoDB replica set, through the replica-set control-plane transaction and
 * the Mongo audit chain. Needs MONGO_IT_URI (rs0).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose, { Model } from 'mongoose';
import { MongoControlPlaneTransaction } from '../../../src/modules/control-plane';
import { CpOrgHeadModel } from '../../../src/libs/repositories/control-plane/org-heads/schema/org-heads.schema';
import { INSTALLATIONS_MIGRATION } from '../../../src/libs/repositories/control-plane/migrations/mongo';
import {
  CpGrantModel,
  CpInstallationHistoryModel,
  CpInstallationModel,
  CpCapabilityLedgerModel,
} from '../../../src/libs/repositories/control-plane/installations/schema/installations.schema';
import { MongoCapabilityLedger } from '../../../src/libs/repositories/control-plane/capability-ledger/mongo';
import { MongoGrantStore } from '../../../src/libs/repositories/control-plane/grant-store/mongo';
import { MongoInstallationStore } from '../../../src/libs/repositories/control-plane/installations/mongo';
import { MigrationLedgerModel } from '../../../src/libs/repositories/control-plane/policy/schema/policy.schema';
import { CapabilityMigration } from '../../../src/modules/control-plane/application/capability-migration';
import { ChainConfigAuditLog } from '../../../src/modules/agent-harness/adapters/chain-config-audit-log';
import { MongoAuditChainStore } from '../../../src/libs/repositories/agent-harness/audit/mongo';
import { DURABLE_TRANSACTION_OPTIONS } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
  resetHarnessMongo,
} from '../agent-harness/support/harness-mongo';
import { describeCapabilityLedgerContract } from './support/capability-ledger-contract';
import { describeCapabilityMigrationCases } from './support/capability-migration-cases';
import { LegacyFacts, migrationDeps, type CapabilityWorld } from './support/capability-world';
import { ORG_A, ORG_B } from './support/cp-objects';

registerHarnessModels([
  CpOrgHeadModel,
  CpInstallationModel,
  CpInstallationHistoryModel,
  CpGrantModel,
  CpCapabilityLedgerModel,
  MigrationLedgerModel,
] as unknown as Array<Model<never>>);

const mongoWorld = async (): Promise<CapabilityWorld> => ({
  installations: new MongoInstallationStore(),
  grants: new MongoGrantStore(),
  ledger: new MongoCapabilityLedger(),
  transaction: new MongoControlPlaneTransaction({
    audit: new ChainConfigAuditLog(new MongoAuditChainStore()),
    transaction: DURABLE_TRANSACTION_OPTIONS,
  }),
  configEntries: async (orgId) =>
    (await new MongoAuditChainStore().read(orgId, 0, 10_000)).filter((record) => record.streamId === `cp:${orgId}`).length,
});

describe('CPD-7 capability migration on a MongoDB replica set', function () {
  this.timeout(120_000);

  before(connectHarnessMongo);
  before(async () => {
    await INSTALLATIONS_MIGRATION.up(mongoose.connection.db!);
  });
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  describeCapabilityLedgerContract('MongoCapabilityLedger', mongoWorld);
  describeCapabilityMigrationCases('Mongo stores', mongoWorld);

  it("CPD10-56: recording and unrecording an org each move its head by one, and the row is the org's one document in cpMigrationLedger", async () => {
    const world = await mongoWorld();
    const migration = new CapabilityMigration(migrationDeps(world, new LegacyFacts()));
    const head = async (orgId: string) => (await CpOrgHeadModel.findById(orgId).lean().exec())?.revision ?? 0;
    await migration.runFor('apply', [ORG_A]);
    expect([await head(ORG_A), await head(ORG_B)]).to.deep.equal([1, 0]);
    expect((await MigrationLedgerModel.find({}).lean().exec()).map((row) => [row._id, row.orgId])).to.deep.equal([
      [`control-plane-capabilities-v1:${ORG_A}`, ORG_A],
    ]);
    await migration.runFor('apply', [ORG_A]);
    expect(await head(ORG_A)).to.equal(1);
    await migration.runFor('revert', [ORG_A]);
    expect(await head(ORG_A)).to.equal(2);
    expect(await MigrationLedgerModel.countDocuments({})).to.equal(0);
  });
});
