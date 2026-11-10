/**
 * MongoSemanticSearchRepository against a real MongoDB, replica set and single server.
 *
 *   MONGO_IT_URI=mongodb://localhost:27017/?replicaSet=rs0 REPLICA_SET_AVAILABLE=true
 *   MONGO_IT_URI=mongodb://localhost:27017                 REPLICA_SET_AVAILABLE=false
 */
import 'reflect-metadata';
import { randomUUID } from 'crypto';
import { expect } from 'chai';
import mongoose from 'mongoose';
import { MongoService } from '../../src/libs/services/mongo.service';
import { MongoUnitOfWork } from '../../src/libs/repositories/mongo';
import { MongoSemanticSearchRepository } from '../../src/libs/repositories/semantic-search/mongo';
import Citation from '../../src/modules/enterprise_search/schema/citation.schema';
import EnterpriseSemanticSearch from '../../src/modules/enterprise_search/schema/search.schema';
import { describeSemanticSearchContract } from './contract/semantic-search.contract';

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} must be set for the MongoDB tests`);
  return value;
}

describe('MongoSemanticSearchRepository against a real MongoDB', function () {
  this.timeout(60000);
  const uri = requireEnv('MONGO_IT_URI');
  const rsFlag = requireEnv('REPLICA_SET_AVAILABLE');
  if (rsFlag !== 'true' && rsFlag !== 'false') {
    throw new Error(`REPLICA_SET_AVAILABLE must be true or false, got '${rsFlag}'`);
  }
  const dbName = `pipeshub_it_${randomUUID().slice(0, 8)}`;
  let mongo: MongoService;
  const repo = new MongoSemanticSearchRepository();
  const newId = () => new mongoose.Types.ObjectId().toHexString();

  before(async () => {
    mongo = new MongoService({ uri, db: dbName });
    await mongo.initialize(); // also sets strictQuery, as the app does
    // Collections must exist before a transaction touches them.
    await EnterpriseSemanticSearch.createCollection();
    await Citation.createCollection();
  });

  after(async () => {
    if (mongo?.isConnected()) {
      await mongo.getConnection().dropDatabase();
      await mongo.destroy();
    }
  });

  describeSemanticSearchContract({
    repo,
    uow: new MongoUnitOfWork(rsFlag === 'true'),
    newId,
    countHits: (ids) => Citation.countDocuments({ _id: { $in: ids } }),
    setCreatedAt: async (id, at) => {
      await EnterpriseSemanticSearch.collection.updateOne(
        { _id: new mongoose.Types.ObjectId(id) },
        { $set: { createdAt: at } },
      );
    },
  });

  describe('row shape (Mongo)', () => {
    it('has the same key set and values as the lean row, ids as strings', async () => {
      const orgId = newId();
      const userId = newId();
      const created = await repo.create({
        orgId,
        userId,
        query: 'shape',
        limit: 3,
        hits: [],
        records: new Map([['a', 'b']]),
      });
      await repo.setSharing(created._id, { isShared: true, shareLink: 'l', sharedWith: [{ userId: newId(), accessLevel: 'read' }] });
      const lean = (await EnterpriseSemanticSearch.findById(created._id).lean())!;
      const dto = await repo.findVisible(orgId, userId, created._id);
      expect(Object.keys(dto!).sort()).to.deep.equal(Object.keys(lean).sort());
      expect(Object.keys(dto!.sharedWith[0]).sort()).to.deep.equal(Object.keys(lean.sharedWith[0]).sort());
      expect(JSON.parse(JSON.stringify(dto))).to.deep.equal(JSON.parse(JSON.stringify(lean)));
      expect(dto!.__v).to.be.a('number');
      // populated read: same as lean + populate
      const leanPop = (await EnterpriseSemanticSearch.findById(created._id)
        .populate({ path: 'citationIds', model: 'citation', select: '-__v' })
        .lean())!;
      const pop = await repo.findVisible(orgId, userId, created._id, { populateCitations: true });
      expect(JSON.parse(JSON.stringify(pop))).to.deep.equal(JSON.parse(JSON.stringify(leanPop)));
    });

    it('serialises a listed row like the document did', async () => {
      const orgId = newId();
      const userId = newId();
      const hitMeta = { orgId, mimeType: 'text/plain', recordId: 'r', recordName: 'n', origin: 'UPLOAD' };
      await repo.create({
        orgId,
        userId,
        query: 'list',
        limit: 3,
        hits: [{ content: 'c', chunkIndex: 0, citationType: 'vector', metadata: hitMeta }],
        records: new Map([['a', 'b']]),
      });
      const doc = (await EnterpriseSemanticSearch.findOne({ orgId, userId }))!;
      const { rows } = await repo.listVisible(orgId, userId, { skip: 0, limit: 5 });
      expect(JSON.parse(JSON.stringify(rows[0]))).to.deep.equal(JSON.parse(JSON.stringify(doc.toJSON())));
    });
  });
});
