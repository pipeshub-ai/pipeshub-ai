/**
 * The access-request contract run through the shadow wrapper (DOC_STORE=shadow): MongoDB answers every call exactly as without the
 * wrapper, and PostgreSQL is asked the same reads behind it. The data exists on MongoDB only, so the shadow reads differ or find
 * nothing: that is counted, and the caller never sees it.
 *
 *   MONGO_IT_URI=mongodb://127.0.0.1:27017 REPLICA_SET_AVAILABLE=false npm run test:pg-stores
 */
import 'reflect-metadata';
import { randomUUID } from 'crypto';
import { expect } from 'chai';
import mongoose from 'mongoose';
import { MongoService } from '../../src/libs/services/mongo.service';
import { MongoUnitOfWork } from '../../src/libs/repositories/mongo';
import { MongoAccessRequestRepository } from '../../src/libs/repositories/access-request/mongo';
import { PgAccessRequestRepository } from '../../src/libs/repositories/access-request/pg';
import { docstoreMetrics } from '../../src/libs/repositories/metrics';
import { shadowWrap, shadowInFlight } from '../../src/libs/repositories/shadow';
import { AccessRequest } from '../../src/libs/repositories/access-request/mongo';
import { describeAccessRequestContract } from './contract/access-request.contract';
import { PG_BINS, PgStack, haveStack, startPgStack } from './pg-store-harness';

const suite = haveStack && process.env.MONGO_IT_URI ? describe : describe.skip;

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`shadow reads over the access-request contract (${bin})`, function () {
    this.timeout(120_000);
    const dbName = `pipeshub_shadow_${randomUUID().slice(0, 8)}`;
    const counts: Record<string, number> = {};
    const real = docstoreMetrics.shadowReads.inc.bind(docstoreMetrics.shadowReads);
    const saved = { s: process.env.DOC_STORE_SHADOW_SAMPLE, t: process.env.DOC_STORE_SHADOW_SETTLE_MS };
    let mongo: MongoService;
    let stack: PgStack;

    before(async () => {
      process.env.DOC_STORE_SHADOW_SAMPLE = '1';
      process.env.DOC_STORE_SHADOW_SETTLE_MS = '10';
      (docstoreMetrics.shadowReads as any).inc = (l: { result: string }) => { counts[l.result] = (counts[l.result] ?? 0) + 1; };
      mongo = new MongoService({ uri: process.env.MONGO_IT_URI as string, db: dbName });
      await mongo.initialize();
      await AccessRequest.createCollection();
      await AccessRequest.syncIndexes();
      stack = await startPgStack(bin, 'shadow_contract');
    });
    after(async () => {
      (docstoreMetrics.shadowReads as any).inc = real;
      if (saved.s === undefined) delete process.env.DOC_STORE_SHADOW_SAMPLE; else process.env.DOC_STORE_SHADOW_SAMPLE = saved.s;
      if (saved.t === undefined) delete process.env.DOC_STORE_SHADOW_SETTLE_MS; else process.env.DOC_STORE_SHADOW_SETTLE_MS = saved.t;
      await stack?.stop();
      if (mongo?.isConnected()) {
        await mongo.getConnection().dropDatabase();
        await mongo.destroy();
      }
    });

    describeAccessRequestContract({
      repo: shadowWrap('access-request', new MongoAccessRequestRepository(), () => new PgAccessRequestRepository()),
      uow: new MongoUnitOfWork(process.env.REPLICA_SET_AVAILABLE === 'true'),
      newId: () => String(new mongoose.Types.ObjectId()),
      rawKeys: async (id) => Object.keys((await AccessRequest.findById(id).lean()) as object),
      setCreatedAt: async (id, at) => {
        await AccessRequest.collection.updateOne({ _id: new mongoose.Types.ObjectId(id) }, { $set: { createdAt: at } });
      },
    });

    it('the shadow reads happened, were bounded, and none of them changed an answer', async () => {
      await new Promise((r) => setTimeout(r, 1500));
      expect(Object.values(counts).reduce((a, b) => a + b, 0)).to.be.greaterThan(0);
      expect(shadowInFlight()).to.equal(0);
    });
  });
}
