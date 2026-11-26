/**
 * MongoDocumentRepository against a real MongoDB (standalone and replica set).
 *
 *   MONGO_IT_URI=mongodb://localhost:27017/?replicaSet=rs0 REPLICA_SET_AVAILABLE=true
 *   MONGO_IT_URI=mongodb://localhost:27017                 REPLICA_SET_AVAILABLE=false
 */
import 'reflect-metadata';
import { randomUUID } from 'crypto';
import { expect } from 'chai';
import mongoose from 'mongoose';
import { MongoService } from '../../src/libs/services/mongo.service';
import { MongoDocumentRepository } from '../../src/libs/repositories/document/mongo';
import { DocumentModel } from '../../src/modules/storage/schema/document.schema';
import { describeDocumentContract } from './contract/document.contract';

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} must be set for the MongoDB tests`);
  return value;
}

describe('MongoDocumentRepository against a real MongoDB', function () {
  this.timeout(60000);
  const uri = requireEnv('MONGO_IT_URI');
  const rsFlag = requireEnv('REPLICA_SET_AVAILABLE');
  if (rsFlag !== 'true' && rsFlag !== 'false') {
    throw new Error(`REPLICA_SET_AVAILABLE must be true or false, got '${rsFlag}'`);
  }
  const dbName = `pipeshub_it_${randomUUID().slice(0, 8)}`;
  let mongo: MongoService;

  before(async () => {
    mongo = new MongoService({ uri, db: dbName });
    await mongo.initialize();
    await DocumentModel.createCollection();
    await DocumentModel.syncIndexes();
  });

  after(async () => {
    if (mongo?.isConnected()) {
      await mongo.getConnection().dropDatabase();
      await mongo.destroy();
    }
  });

  describeDocumentContract({
    repo: new MongoDocumentRepository(),
    newId: () => String(new mongoose.Types.ObjectId()),
    setHiddenState: async (orgId, id, state) => {
      const r = await DocumentModel.collection.updateOne(
        { _id: new mongoose.Types.ObjectId(id), orgId: new mongoose.Types.ObjectId(orgId) },
        { $set: { writeSeq: state.writeSeq, writers: state.writers } },
      );
      if (r.matchedCount !== 1) throw new Error(`no row ${id} to set the write state on`);
    },
    getHiddenState: async (orgId, id) => {
      const raw = await DocumentModel.collection.findOne({
        _id: new mongoose.Types.ObjectId(id),
        orgId: new mongoose.Types.ObjectId(orgId),
      });
      return { writeSeq: raw?.writeSeq as number, writers: raw?.writers as { t: string; until: number }[] };
    },
  });

  // A build from before writes had their own sets declares the same unique index on (orgId, vrid, rev) and builds it at boot
  // (Model.init); a failed build would fail every keyed create. Several write sets of one revision must not stop it.
  it('rebuilds the unique artifact-set index over several stored writes of one revision', async () => {
    const orgId = new mongoose.Types.ObjectId();
    const set = (key: string, extra: Record<string, string> = {}) => ({
      orgId, documentName: `revision_${key}`, documentPath: `${orgId}/PipesHub/record-revisions/v1`, isVersionedFile: false,
      artifactSet: { virtualRecordId: 'v1', rev: key, root: `${orgId}/PipesHub/record-revisions/v1/${key}`, ...extra },
    });
    const W1 = 'a'.repeat(32);
    const W2 = 'b'.repeat(32);
    await DocumentModel.collection.insertMany([
      set('r1'),
      set(`r1-${W1}`, { contentRev: 'r1', writeId: W1 }),
      set(`r1-${W2}`, { contentRev: 'r1', writeId: W2 }),
    ]);
    const key = { orgId: 1, 'artifactSet.virtualRecordId': 1, 'artifactSet.rev': 1 };
    const named = (await DocumentModel.collection.indexes()).find((i) => JSON.stringify(i.key) === JSON.stringify(key));
    expect(named?.unique).to.equal(true);
    await DocumentModel.collection.dropIndex(named!.name!);
    await DocumentModel.syncIndexes();
    const rebuilt = (await DocumentModel.collection.indexes()).find((i) => JSON.stringify(i.key) === JSON.stringify(key));
    expect(rebuilt?.unique).to.equal(true);
    // The index still refuses a second row of one key.
    let refused: unknown;
    await DocumentModel.collection.insertOne(set(`r1-${W1}`)).catch((e: unknown) => { refused = e; });
    expect((refused as { code?: number } | undefined)?.code).to.equal(11000);
  });
});
