/**
 * WP-23 (OWD-12) on a MongoDB replica set. Run as execution-store.mongo.itest.ts says.
 *
 * RES-9: every stored harness document carries the deployment's region, however it is inserted (create,
 * insertMany, an upsert, a bulk write), and an update never moves it. RES-10: the claim query hands a worker only
 * runs of its region (a run from before regions is the Node's region's). RES-11: the snapshot index (SNT-5,
 * SNT-7..SNT-9) on Mongo, by its contract.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose, { Model } from 'mongoose';
import { MongoSandboxSnapshotIndex } from '../../../src/libs/repositories/agent-harness/sandbox-snapshots/mongo';
import { AgentSandboxReapModel } from '../../../src/libs/repositories/agent-harness/pending-reaps/schema/pending-reaps.schema';
import { AgentSandboxSnapshotModel } from '../../../src/libs/repositories/agent-harness/sandbox-snapshots/schema/sandbox-snapshots.schema';
import { HARNESS_MIGRATIONS } from '../../../src/libs/repositories/agent-harness/migrations/mongo';
import { REGION_PATH } from '../../../src/libs/repositories/agent-harness/schema/harness-model.schema';
import { placedExpr } from '../../../src/libs/repositories/agent-harness/worker-compat-expr/mongo';
import { claimingWorker, REGION_AWARE_FEATURE } from '../../../src/modules/agent-harness/domain/worker-compat';
import { deploymentRegion } from '../../../src/modules/agent-harness/domain/region';
import { CURRENT_WORKER_CAPABILITIES } from './support/worker-capabilities';
import { describeSandboxSnapshotIndexContract } from './support/sandbox-snapshots-contract';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
} from './support/harness-mongo';

registerHarnessModels([AgentSandboxReapModel, AgentSandboxSnapshotModel] as unknown as Array<
  Model<never>
>);

const HERE = deploymentRegion();
const reap = (id: string) => ({
  _id: id,
  orgId: 'org-1',
  labels: {},
  failures: 0,
  nextAttemptAt: 0,
  createdAt: 0,
  lastErrorType: null,
});

describe('WP-23 regions and the snapshot index on a MongoDB replica set (OWD-12)', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  it('RES-9: every way a harness document is inserted stamps the region; an update keeps it', async () => {
    await AgentSandboxReapModel.create(reap('created'));
    await AgentSandboxReapModel.insertMany([reap('many')]);
    await AgentSandboxReapModel.updateOne({ _id: 'upserted' }, { $setOnInsert: reap('upserted') }, { upsert: true });
    await AgentSandboxReapModel.bulkWrite([
      { insertOne: { document: reap('bulk-insert') } },
      { updateOne: { filter: { _id: 'bulk-upsert' }, update: { $setOnInsert: reap('bulk-upsert') }, upsert: true } },
    ]);
    await AgentSandboxReapModel.collection.updateOne({ _id: 'created' as never }, { $set: { region: 'moved-away' } });
    await AgentSandboxReapModel.updateOne({ _id: 'created' }, { $set: { failures: 1 } });
    const stored = await AgentSandboxReapModel.collection.find({}).toArray();
    expect(Object.fromEntries(stored.map((doc) => [String(doc._id), doc[REGION_PATH]]))).to.deep.equal({
      created: 'moved-away',
      many: HERE,
      upserted: HERE,
      'bulk-insert': HERE,
      'bulk-upsert': HERE,
    });
  });

  it('RES-9: every harness model declares the region path', () => {
    const harness = Object.values(mongoose.models).filter((model) =>
      ['Agent', 'SessionEvent', 'SessionStream'].some((prefix) => model.modelName.startsWith(prefix)),
    );
    expect(harness.length).to.be.greaterThan(5);
    for (const model of harness) {
      expect(model.schema.path(REGION_PATH), model.modelName).to.not.equal(undefined);
    }
  });

  it("RES-10: the claim query places a run only on its region's worker", async () => {
    const collection = mongoose.connection.db!.collection('wp23_placement');
    await collection.insertMany([{ _id: 'eu', region: 'eu-west' }, { _id: 'us', region: 'us-east' }, { _id: 'old' }] as never);
    const beforeRegions = {
      ...CURRENT_WORKER_CAPABILITIES,
      features: CURRENT_WORKER_CAPABILITIES.features.filter((f) => f !== REGION_AWARE_FEATURE),
    };
    const placed = async (worker: string | undefined, here: string, advertised = CURRENT_WORKER_CAPABILITIES) =>
      (
        await collection
          .find({ $expr: placedExpr(claimingWorker(advertised, { worker, here })) })
          .toArray()
      )
        .map((doc) => String(doc._id))
        .sort();
    expect(await placed('eu-west', 'eu-west')).to.deep.equal(['eu', 'old']);
    expect(await placed('us-east', 'eu-west')).to.deep.equal(['us']);
    // A worker that knows regions and names none is no region's, so it runs nothing in a regioned deployment
    // (OWD-12 fails closed); one older than regions is this Node's.
    expect(await placed(undefined, 'eu-west')).to.deep.equal([]);
    expect(await placed(undefined, 'eu-west', beforeRegions)).to.deep.equal(['eu', 'old']);
    expect(
      (await collection.find({ $expr: placedExpr(claimingWorker(CURRENT_WORKER_CAPABILITIES)) }).toArray()).length,
    ).to.equal(3);
  });

  it('the sandbox snapshots migration is registered', () => {
    expect(HARNESS_MIGRATIONS.map((migration) => migration.id)).to.include('sandbox-snapshots');
  });

  // RES-11: the snapshot index (SNT-5, SNT-7..SNT-9) on Mongo.
  describeSandboxSnapshotIndexContract('mongo replica set', () => new MongoSandboxSnapshotIndex());
});
