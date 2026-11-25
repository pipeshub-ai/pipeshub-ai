/**
 * LCFIX, GAPS-1: the typed query, the attachments, the filters and the caller
 * are sealed in `agentRuns`, `agentAdmissionReceipts`, `agentCommandReceipts`
 * and a schedule's template in `agentSchedules`, read back where they are
 * needed, and rows stored before are sealed by the boot migration. Runs as
 * execution-store.mongo.itest.ts (a throwaway database, dropped after).
 */
import { expect } from 'chai';
import mongoose from 'mongoose';
import { MongoScheduleStore } from '../../../src/libs/repositories/agent-harness/schedules/mongo';
import { mongoServerTime } from '../../../src/libs/repositories/agent-harness/time/mongo';
import { sealStoredRunInputs } from '../../../src/libs/repositories/agent-harness/migrations/mongo';
import { RunInputSeal } from '../../../src/modules/agent-harness/application/run-input-seal';
import {
  contractClock,
  ContractHarness,
  createContractHarness,
} from './support/execution-store-contract';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  mongoBackend,
  resetHarnessMongo,
} from './support/harness-mongo';
import { testCipher } from './support/data-protection';
import { aSchedule } from './support/schedules';

const MARKER = 'lcfix-marker-A';
// GAPS-1: each piece of the person's content carries its own marker.
const DETAIL_MARKERS = ['gaps-attachment-B', 'gaps-filter-C', 'gaps-applied-D', 'gaps-email-E', 'gaps-name-F'];
// GAPS2-1: the history a run holds when it is not kept by reference.
const HISTORY_MARKER = 'gaps2-history-G';
const RUN_COLLECTIONS =['agentRuns', 'agentAdmissionReceipts', 'agentCommandReceipts'];
const COLLECTIONS = [...RUN_COLLECTIONS, 'agentSchedules'];
const input = (query: string) => ({
  query,
  recordIds: [],
  filters: { kb: ['gaps-filter-C'] },
  appliedFilters: { kb: [{ id: 'kb-1', name: 'gaps-applied-D' }] },
  attachments: [{ recordId: 'att-1', recordName: 'gaps-attachment-B.pdf', mimeType: 'application/pdf' }],
  caller: { email: 'gaps-email-E@example.com', displayName: 'gaps-name-F' },
  model: { chatMode: 'auto' },
});

const db = () => mongoose.connection.db!;

/** Every document of `collection` holding `text` anywhere, as stored. */
const holding = async (collection: string, text: string): Promise<number> =>
  (await db().collection(collection).find({}).toArray()).filter((d) => JSON.stringify(d).includes(text)).length;

describe('LCFIX: the typed query rests sealed in the harness collections', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  let h: ContractHarness;
  beforeEach(async () => {
    await resetHarnessMongo();
    h = createContractHarness(mongoBackend(), contractClock());
    h.setNow(await mongoServerTime.read());
  });

  it('admission, claim, commit and both replays: the worker and replays read the text, no collection holds it', async () => {
    const key = 'lcfix-idempotency';
    const admitted = await h.admit({ idempotencyKey: key, input: input(`${MARKER} add up my notes`) });
    const replay = await h.admit({ idempotencyKey: key, input: input(`${MARKER} add up my notes`) });
    expect(replay.run).to.deep.equal(admitted.run);
    expect(replay.run.input?.query).to.equal(`${MARKER} add up my notes`);
    expect(replay.run.input).to.deep.include({ attachments: input('').attachments, caller: input('').caller });

    const claimed = await h.claim(admitted.run, 'worker-a', 60_000);
    expect(claimed.input?.query).to.equal(`${MARKER} add up my notes`);
    expect(claimed.input).to.deep.include({ filters: input('').filters, appliedFilters: input('').appliedFilters });
    expect((await h.service.get(claimed.orgId, claimed.runId))?.input?.query).to.equal(`${MARKER} add up my notes`);

    const command = {
      commandId: 'lcfix-commit', orgId: claimed.orgId, runId: claimed.runId, workerId: 'worker-a',
      attemptId: claimed.attemptId as string, fence: claimed.fence, expectedRevision: claimed.revision,
      status: 'completed' as const, outcome: 'answer' as const, outbox: [],
      events: [{ type: 'result', data: { n: 1 } }],
    };
    const first = await h.service.commit(command);
    expect(await h.service.commit(command)).to.deep.equal({ ...first, replayed: true });

    const schedules = new MongoScheduleStore(mongoServerTime, new RunInputSeal(testCipher()));
    const template = aSchedule({ input: input(`${MARKER} weekly digest`) });
    await schedules.create(template, { perActor: 10, perOrg: 10 });
    expect((await schedules.get(template.orgId, template.id))?.input).to.deep.equal(template.input);
    expect((await schedules.list(template.orgId, {}, 10))[0]?.input).to.deep.equal(template.input);

    for (const collection of COLLECTIONS) {
      expect(await db().collection(collection).countDocuments({}), collection).to.be.greaterThan(0);
      for (const marker of [MARKER, ...DETAIL_MARKERS]) {
        expect(await holding(collection, marker), `${collection} ${marker}`).to.equal(0);
      }
    }
  });

  it('the boot migration seals rows stored before, once, and two replicas racing seal each row once', async () => {
    const run = (await h.admit({ input: input('placeholder') })).run;
    const legacy = { orgId: run.orgId, runId: run.runId, input: input(`${MARKER} legacy`) };
    await db().collection('agentRuns').updateOne(
      { runId: run.runId },
      { $set: { input: legacy.input } },
    );
    // A schedule stored before GAPS-1: its template in plaintext.
    const template = aSchedule({ input: input(`${MARKER} legacy digest`) });
    await new MongoScheduleStore().create(template, { perActor: 10, perOrg: 10 });
    // A run whose query LCFIX already sealed, its details still in plaintext.
    const lcfix = (await h.admit({ input: input('second') })).run;
    const sealedQuery = (await db().collection('agentRuns').findOne({ runId: lcfix.runId }))!['input']['querySealed'];
    const { query: _unused, ...details } = input('unused');
    _unused;
    await db().collection('agentRuns').updateOne(
      { runId: lcfix.runId },
      { $set: { input: { ...details, querySealed: sealedQuery } } },
    );
    await db().collection('agentAdmissionReceipts').insertOne({ scopeKey: 'lcfix-legacy', payloadHash: 'h', result: { run: legacy }, createdAt: 1 });
    await db().collection('agentCommandReceipts').insertOne({
      orgId: run.orgId, runId: run.runId, commandId: 'lcfix-legacy', payloadHash: 'h', receipt: { run: legacy }, createdAt: 1,
    });
    for (const collection of COLLECTIONS) expect(await holding(collection, MARKER), collection).to.equal(1);
    expect(await holding('agentRuns', 'gaps-email-E'), 'agentRuns details').to.equal(2);

    const seal = new RunInputSeal(testCipher());
    const [a, b] = await Promise.all([sealStoredRunInputs(seal, db()), sealStoredRunInputs(seal, db())]);
    for (const collection of COLLECTIONS) {
      const rows = collection === 'agentRuns' ? 2 : 1;
      expect((a[collection] ?? 0) + (b[collection] ?? 0), collection).to.equal(rows);
      for (const marker of [MARKER, ...DETAIL_MARKERS]) {
        expect(await holding(collection, marker), `${collection} ${marker}`).to.equal(0);
      }
    }
    expect(Object.values(await sealStoredRunInputs(seal, db()))).to.deep.equal([0, 0, 0, 0]);

    expect((await h.service.get(run.orgId, run.runId))?.input).to.deep.include(legacy.input);
    expect((await h.service.get(lcfix.orgId, lcfix.runId))?.input).to.deep.include({ ...input('second') });
    const schedules = new MongoScheduleStore(mongoServerTime, seal);
    expect((await schedules.get(template.orgId, template.id))?.input).to.deep.equal(template.input);
    const receipt = await db().collection('agentCommandReceipts').findOne({ commandId: 'lcfix-legacy' });
    const opened = await seal.open((receipt as unknown as { receipt: { run: typeof legacy } }).receipt.run);
    expect(opened.input.query).to.equal(`${MARKER} legacy`);
  });

  it('GAPS2-1: the boot migration seals an inline history, beside details GAPS-1 sealed or plaintext ones, once across two replicas', async () => {
    const history = [{ role: 'user_query', content: `${HISTORY_MARKER} earlier question` }];
    // A run whose query and details GAPS-1 sealed, its history inline in plaintext (by reference off).
    const gaps1 = (await h.admit({ input: input('gaps1') })).run;
    const gaps1Input = (await db().collection('agentRuns').findOne({ runId: gaps1.runId }))!['input'] as Record<string, unknown>;
    await db().collection('agentRuns').updateOne({ runId: gaps1.runId }, { $set: { 'input.previousConversations': history } });
    // A run stored before LCFIX: everything in plaintext.
    const legacy = (await h.admit({ input: input('placeholder') })).run;
    const legacyInput = { ...input(`${MARKER} legacy`), previousConversations: history };
    await db().collection('agentRuns').updateOne({ runId: legacy.runId }, { $set: { input: legacyInput } });
    await db().collection('agentAdmissionReceipts').insertOne({
      scopeKey: 'gaps2-legacy',
      payloadHash: 'h',
      result: { run: { orgId: legacy.orgId, runId: legacy.runId, input: legacyInput } },
      createdAt: 1,
    });
    expect(await holding('agentRuns', HISTORY_MARKER)).to.equal(2);

    const seal = new RunInputSeal(testCipher());
    const [a, b] = await Promise.all([sealStoredRunInputs(seal, db()), sealStoredRunInputs(seal, db())]);
    expect((a['agentRuns'] ?? 0) + (b['agentRuns'] ?? 0)).to.equal(2);
    expect((a['agentAdmissionReceipts'] ?? 0) + (b['agentAdmissionReceipts'] ?? 0)).to.equal(1);
    for (const collection of RUN_COLLECTIONS) {
      for (const marker of [HISTORY_MARKER, MARKER, ...DETAIL_MARKERS]) {
        expect(await holding(collection, marker), `${collection} ${marker}`).to.equal(0);
      }
    }
    expect(Object.values(await sealStoredRunInputs(seal, db()))).to.deep.equal([0, 0, 0, 0]);

    const resealed = (await db().collection('agentRuns').findOne({ runId: gaps1.runId }))!['input'] as Record<string, unknown>;
    expect(resealed['detailsSealed'], 'the sealed details are left as they were').to.deep.equal(gaps1Input['detailsSealed']);
    expect(resealed).to.have.property('historySealed');
    expect((await h.service.get(gaps1.orgId, gaps1.runId))?.input).to.deep.include({ ...input('gaps1'), previousConversations: history });
    expect((await h.service.get(legacy.orgId, legacy.runId))?.input).to.deep.include(legacyInput);
    const receipt = await db().collection('agentAdmissionReceipts').findOne({ scopeKey: 'gaps2-legacy' });
    const opened = await seal.open((receipt as unknown as { result: { run: { orgId: string; runId: string; input: never } } }).result.run);
    expect(opened.input).to.deep.include(legacyInput);
  });
});
