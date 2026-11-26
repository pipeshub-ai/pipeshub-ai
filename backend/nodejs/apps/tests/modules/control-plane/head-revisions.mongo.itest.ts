/**
 * CPD-5 on a MongoDB replica set: the `HeadRevisions` contract (HR) on
 * `MongoHeadRevisions`, the heads moved by the Mongo control-plane
 * transaction's own head claim. Run as execution-store.mongo.itest.ts says.
 */
import 'reflect-metadata';
import { Model } from 'mongoose';
import { CpOrgHeadModel } from '../../../src/libs/repositories/control-plane/org-heads/schema/org-heads.schema';
import {
  MongoDeploymentRevision,
  MongoHeadRevisions,
} from '../../../src/libs/repositories/control-plane/head-revisions/mongo';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
  resetHarnessMongo,
} from '../agent-harness/support/harness-mongo';
import { committingTx, describeHeadRevisionsContract } from './support/head-revisions-contract';

registerHarnessModels([CpOrgHeadModel] as unknown as Array<Model<never>>);

describe('CPD-5 MongoHeadRevisions', () => {
  before(connectHarnessMongo);

  after(async () => {
    await disconnectHarnessMongo();
  });

  beforeEach(resetHarnessMongo);

  describeHeadRevisionsContract('MongoHeadRevisions', async () => ({
    heads: new MongoHeadRevisions(),
    // What MongoControlPlaneTransaction does first in every write.
    commit: async (orgId) => {
      await CpOrgHeadModel.findOneAndUpdate(
        { _id: orgId },
        { $inc: { revision: 1 }, $set: { updatedAt: new Date() } },
        { upsert: true, new: true },
      );
    },
    moveDeployment: () => new MongoDeploymentRevision().move(committingTx('any')),
  }));
});
