/**
 * G2 (ADR-INBOX-01) on a real MongoDB replica set: the InboxStore contract on `MongoInboxStore`, and its indexes as
 * the `inbox` migration builds them (run instructions in execution-store.mongo.itest.ts).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Model } from 'mongoose';
import { MongoInboxStore } from '../../../src/libs/repositories/agent-harness/inbox/mongo';
import {
  InboxPreferencesModel,
  RunEndNoticeClaimModel,
  RunWatchLeaseModel,
} from '../../../src/libs/repositories/agent-harness/inbox/schema/inbox.schema';
import { describeInboxStoreContract } from './support/inbox-store-contract';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
  resetHarnessMongo,
} from './support/harness-mongo';

registerHarnessModels([InboxPreferencesModel, RunEndNoticeClaimModel, RunWatchLeaseModel] as unknown as Array<Model<never>>);

describe('G2 Inbox store on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  describeInboxStoreContract('mongo replica set', async () => {
    await resetHarnessMongo();
    return {
      store: new MongoInboxStore(),
      replica: () => new MongoInboxStore(),
      orgs: ['6650a1b2c3d4e5f607182930', '6650a1b2c3d4e5f607182931'],
      users: ['6650a1b2c3d4e5f607182940', '6650a1b2c3d4e5f607182941'],
    };
  });

  it('IBX-M1: a lease and a claim carry the TTL fields the server expires them by', async () => {
    const store = new MongoInboxStore();
    await store.watch({ orgId: 'o', userId: 'u', sessionId: 's', watcherId: 'w', until: 5_000 });
    await store.claimRunNotice('o', 'r', 'u', 7_000);
    expect((await RunWatchLeaseModel.findById('o:w').lean())?.expiresAt?.getTime()).to.equal(5_000);
    expect((await RunEndNoticeClaimModel.findById('o:r').lean())?.createdAt?.getTime()).to.equal(7_000);
  });
});
