/**
 * Two Mongo readers that no replica-set test called: the run status the
 * approval watch reads (`MongoRunStatusReader`), and the orgs that hold
 * harness data, which the retention sweeper walks to collect discarded
 * drafts (`MongoRetentionStore.harnessOrgs`).
 *
 *   MONGO_IT_URI="mongodb://localhost:<port>/x?replicaSet=rs0" REPLICA_SET_AVAILABLE=true \
 *     TS_NODE_PROJECT=tsconfig.test.json npx mocha --no-config --require reflect-metadata \
 *     --require ts-node/register --exit --timeout 60000 tests/modules/agent-harness/store-readers.mongo.itest.ts
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { MongoDataKeyStore } from '../../../src/libs/repositories/agent-harness/data-protection/mongo';
import { MongoRetentionStore } from '../../../src/libs/repositories/agent-harness/retention-store/mongo';
import { MongoRunStatusReader } from '../../../src/libs/repositories/agent-harness/run-status/mongo';
import { dataKeyRing } from './support/data-protection';
import { contractClock, createContractHarness } from './support/execution-store-contract';
import { connectHarnessMongo, disconnectHarnessMongo, mongoBackend, resetHarnessMongo } from './support/harness-mongo';
import { HARNESS_ENABLED } from './support/rollout';

const TURN = { input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } } };

describe('harness store readers on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  it('a run\'s status is read by its org and id, as it changes; another org and an unknown run read nothing', async () => {
    const clock = contractClock();
    const h = createContractHarness(mongoBackend({ clock }), clock);
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 as const });
    const { run } = await h.admit(TURN);
    const reader = new MongoRunStatusReader();

    expect(await reader.status(run.orgId, run.runId)).to.equal('queued');
    await h.claim(run);
    expect(await reader.status(run.orgId, run.runId)).to.equal('running');
    expect(await reader.status('another-org', run.runId)).to.equal(null);
    expect(await reader.status(run.orgId, 'run-unknown')).to.equal(null);
  });

  it('the orgs that hold harness data are those with a data key, each once', async () => {
    const store = new MongoRetentionStore();
    expect(await store.harnessOrgs()).to.deep.equal([]);
    const { cipher } = dataKeyRing({ store: new MongoDataKeyStore() });
    for (const [orgId, n] of [['org-a', 1], ['org-b', 2], ['org-a', 3]] as const) {
      await cipher.seal(orgId, 'journal.data', { n }, Buffer.from('x'));
    }
    expect((await store.harnessOrgs()).sort()).to.deep.equal(['org-a', 'org-b']);
  });
});
