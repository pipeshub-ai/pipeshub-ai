/**
 * P3.2 on a real MongoDB replica set (run instructions in
 * execution-store.mongo.itest.ts): the stores contract (NW-9), concurrent
 * commits on one base (NW-10, in the contract) and the D2 migration up, down, up (NW-11).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose, { Model } from 'mongoose';
import {
  MongoWorkspaceKeyStore,
  MongoWorkspaceRepository,
  MongoWorkspaceWriterStore,
} from '../../../src/libs/repositories/agent-harness/workspace/mongo';
import {
  migrateWorkspaceStore,
  revertWorkspaceStore,
} from '../../../src/libs/repositories/agent-harness/migrations/mongo';
import { WORKSPACE_WRITERS_MIGRATION } from '../../../src/libs/repositories/agent-harness/migrations/mongo';
import {
  AgentWorkspaceKeyModel,
  AgentWorkspaceManifestModel,
  AgentWorkspaceWriterModel,
} from '../../../src/libs/repositories/agent-harness/workspace/schema/workspace.schema';
import { migrateHarnessStore } from '../../../src/libs/repositories/agent-harness/journal-startup/mongo';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
  resetHarnessMongo,
} from './support/harness-mongo';
import {
  describeWorkspaceStoresContract,
  storedManifest,
} from './support/workspace-repository-contract';

registerHarnessModels([
  AgentWorkspaceManifestModel,
  AgentWorkspaceKeyModel,
  AgentWorkspaceWriterModel,
] as unknown as Array<Model<never>>);

describe('P3.2 workspace store on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  describeWorkspaceStoresContract('mongo', async () => ({
    repository: new MongoWorkspaceRepository(),
    keys: new MongoWorkspaceKeyStore(),
    writers: new MongoWorkspaceWriterStore(),
  }));

  it('P6.4 NF-3: the workspace-writers migration goes up, down and up again', async () => {
    const db = mongoose.connection.db!;
    await db.collection('agentWorkspaceWriters').dropIndexes().catch(() => undefined);
    expect((await WORKSPACE_WRITERS_MIGRATION.up(db)).created).to.deep.equal([
      'agentWorkspaceWriters.org_workspace',
    ]);
    expect((await WORKSPACE_WRITERS_MIGRATION.up(db)).created).to.deep.equal([]);
    const index = (await db.collection('agentWorkspaceWriters').indexes()).find(
      (candidate) => candidate.name === 'org_workspace',
    );
    expect(index?.unique).to.equal(true);
    expect((await WORKSPACE_WRITERS_MIGRATION.down(db)).dropped).to.have.length(1);
    await migrateHarnessStore(db);
    expect(
      (await db.collection('agentWorkspaceWriters').indexes()).map((i) => i.name),
    ).to.include('org_workspace');
  });

  it('NW-11: the D2 migration goes up, down and up again', async () => {
    const db = mongoose.connection.db!;
    const names = async (collection: string): Promise<string[]> =>
      (await db.collection(collection).indexes()).map((index) =>
        String(index.name),
      );
    await db.collection('agentWorkspaceManifests').dropIndexes();
    await db.collection('agentWorkspaceKeys').dropIndexes();

    const up = await migrateWorkspaceStore(db);
    expect(up.created).to.deep.equal([
      'agentWorkspaceManifests.org_workspace_revision',
      'agentWorkspaceKeys.org_session',
    ]);
    expect((await migrateWorkspaceStore(db)).created).to.deep.equal([]);
    const manifestIndex = (
      await db.collection('agentWorkspaceManifests').indexes()
    ).find((index) => index.name === 'org_workspace_revision');
    expect(manifestIndex?.unique).to.equal(true);

    await new MongoWorkspaceRepository().append({
      manifest: storedManifest('ws:s3', 1),
      expectedRevision: 0,
    });
    const down = await revertWorkspaceStore(db);
    expect(down.dropped).to.have.length(2);
    expect(await names('agentWorkspaceManifests')).to.deep.equal(['_id_']);
    expect(await names('agentWorkspaceKeys')).to.deep.equal(['_id_']);
    expect(await AgentWorkspaceManifestModel.countDocuments({})).to.equal(1);
    expect((await revertWorkspaceStore(db)).dropped).to.deep.equal([]);

    await migrateHarnessStore(db);
    expect(await names('agentWorkspaceManifests')).to.include(
      'org_workspace_revision',
    );
    expect(await names('agentWorkspaceKeys')).to.include('org_session');
  });
});
