/**
 * The MongoDB agent-call repository, judged by the queries it sends (no database here: the same behaviour against a real
 * MongoDB is tests/stores/mongo-agent-call-event.itest.ts).
 */
import { expect } from 'chai';
import mongoose from 'mongoose';
import sinon from 'sinon';
import { MongoAgentCallRepository } from '../../../src/libs/repositories/agent-call-event/mongo';
import { minuteOf, type NewAgentCall } from '../../../src/libs/repositories/agent-call-event/types';
import { AgentCallEvent } from '../../../src/modules/oauth_provider/schema/agent-call-event.schema';

const org = '650000000000000000000001';
const user = '650000000000000000000002';
const call = (rollup: boolean): NewAgentCall =>
  ({
    orgId: org,
    userId: user,
    tokenId: 't1',
    tokenKind: 'pat',
    method: 'GET',
    route: '/api/v1/x',
    statusCode: 200,
    rollup,
  }) as unknown as NewAgentCall;

describe('MongoAgentCallRepository (queries)', () => {
  const repo = new MongoAgentCallRepository();
  afterEach(() => sinon.restore());

  it('creates one event with a count of 1 when the call is not rolled up', async () => {
    const create = sinon.stub(AgentCallEvent, 'create').resolves();
    await repo.record(call(false));
    const doc = create.firstCall.args[0] as unknown as Record<string, unknown>;
    expect(doc.count).to.equal(1);
    expect(String(doc.orgId)).to.equal(org);
    expect(doc).to.not.have.property('rollup');
  });

  it("counts a rolled-up call into its minute's row, keyed on org, token, method and route", async () => {
    const update = sinon.stub(AgentCallEvent, 'updateOne').resolves();
    await repo.record(call(true));
    const [key, change, options] = update.firstCall.args as unknown as [Record<string, unknown>, Record<string, Record<string, unknown>>, unknown];
    expect(Object.keys(key).sort()).to.deep.equal(['bucket', 'method', 'orgId', 'route', 'tokenId']);
    expect(key.bucket).to.deep.equal(minuteOf(key.bucket as Date));
    expect(change.$inc).to.deep.equal({ count: 1 });
    expect(change.$setOnInsert).to.not.have.any.keys('orgId', 'tokenId', 'method', 'route');
    expect(options).to.deep.equal({ upsert: true });
  });

  it('retries once when a racing upsert took the new minute, and rethrows any other error', async () => {
    const update = sinon.stub(AgentCallEvent, 'updateOne');
    update.onFirstCall().rejects(Object.assign(new Error('dup'), { code: 11000 }));
    update.onSecondCall().resolves();
    await repo.record(call(true));
    expect(update.callCount).to.equal(2);

    update.reset();
    update.rejects(Object.assign(new Error('down'), { code: 6 }));
    let caught: unknown;
    await repo.record(call(true)).catch((e: unknown) => { caught = e; });
    expect((caught as Error).message).to.equal('down');
    expect(update.callCount).to.equal(1);
  });

  it('lists the org newest first, with every filter and the page cursor, and drops the internal fields', async () => {
    let sent: Record<string, unknown> = {};
    const chain = {
      sort: (s: unknown) => { sent.sort = s; return chain; },
      limit: (n: number) => { sent.limit = n; return chain; },
      lean: () => chain,
      exec: async () => [{ _id: 'e1', route: '/x', bucket: new Date(0), __v: 0 }],
    };
    sinon.stub(AgentCallEvent, 'find').callsFake(((q: unknown) => { sent = { q }; return chain; }) as never);
    const before = { createdAt: new Date(2_000), id: '650000000000000000000003' };
    const rows = await repo.list(org, {
      from: new Date(1_000), to: new Date(3_000), userId: user, tokenId: 't1', tokenKind: 'pat',
      client: 'cli', statusClass: '4xx', before,
    } as never, 25);

    const and = (sent.q as { $and: Record<string, unknown>[] }).$and;
    expect(String((and[0]!.orgId as { $eq: mongoose.Types.ObjectId }).$eq)).to.equal(org);
    expect(and).to.deep.include({ createdAt: { $gte: new Date(1_000), $lte: new Date(3_000) } });
    expect(and).to.deep.include({ statusCode: { $gte: 400, $lt: 500 } });
    expect(and).to.deep.include({ userAgent: { $eq: 'cli' } });
    expect(and).to.have.length(8);
    expect(sent.sort).to.deep.equal({ createdAt: -1, _id: -1 });
    expect(sent.limit).to.equal(25);
    expect(rows).to.deep.equal([{ _id: 'e1', route: '/x' }]);
  });
});
