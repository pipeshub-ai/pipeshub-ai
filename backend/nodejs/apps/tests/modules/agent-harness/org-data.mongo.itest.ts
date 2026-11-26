/**
 * `MongoOrgData` and `OutboxOrgDeletedAnnouncer` (CLEANUP-G1) on a MongoDB replica set: the reads and erases an
 * org's erasure job runs, one store at a time. The job's own order of steps is `principal-erasure.test.ts`'s; here
 * each step is checked against real collections: what it finds or removes for the org, and that another org, the
 * audit chain and the data keys are left to their own steps.
 * Run as execution-store.mongo.itest.ts says.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose, { Types } from 'mongoose';
import { randomUUID } from 'node:crypto';
import { connectHarnessMongo, disconnectHarnessMongo } from './support/harness-mongo';
import { MongoOrgData } from '../../../src/libs/repositories/agent-harness/principal-erasure/mongo';
import { OutboxOrgDeletedAnnouncer } from '../../../src/libs/repositories/agent-harness/org-deleted-announcer/mongo';
import { inMongoDurableTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { OutboxEvent } from '../../../src/libs/repositories/outbox/schema/outbox.schema';

const oid = (): string => new Types.ObjectId().toHexString();
const col = (name: string) => mongoose.connection.collection(name);
const insert = (name: string, docs: Array<Record<string, unknown>>) => col(name).insertMany(docs as never[]);
const count = (name: string, filter: Record<string, unknown>) => col(name).countDocuments(filter as never);

describe('org data of the org erasure on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  describe('sessionsAfter', () => {
    it("pages the org's conversations and the sessions only its runs, journals and workspaces name, in id order", async () => {
      const [org, other] = [oid(), oid()];
      const sessions = [oid(), oid(), oid()].sort();
      await insert('chatSessions', [
        ...sessions.map((id) => ({ _id: new Types.ObjectId(id), orgId: new Types.ObjectId(org) })),
        { _id: new Types.ObjectId(), orgId: new Types.ObjectId(other) },
      ]);
      await insert('agentRuns', [
        { orgId: org, runId: randomUUID(), sessionId: 's-run-only', status: 'completed' },
        { orgId: other, runId: randomUUID(), sessionId: 's-theirs', status: 'completed' },
      ]);
      // A session named twice (a conversation and a workspace key) is listed once.
      await insert('agentWorkspaceKeys', [{ orgId: org, sessionId: sessions[1] }]);
      await insert('agentEventCounters', [{ orgId: org, sessionId: 's-counter-only' }]);
      const data = new MongoOrgData();

      expect(await data.sessionsAfter(org, null, 10)).to.deep.equal([...sessions, 's-counter-only', 's-run-only']);
      expect(await data.sessionsAfter(org, null, 2)).to.deep.equal(sessions.slice(0, 2));
      expect(await data.sessionsAfter(org, sessions[1]!, 10)).to.deep.equal([sessions[2], 's-counter-only', 's-run-only']);
      // A cursor that is no conversation id: the conversations are read whole, then cut at the cursor.
      expect(await data.sessionsAfter(org, 's-d', 10)).to.deep.equal(['s-run-only']);
    });

    it('an org id that is not an ObjectId has no conversations, only the sessions its runs name', async () => {
      await insert('agentEventCounters', [{ orgId: 'org-plain', sessionId: 'e1' }]);
      expect(await new MongoOrgData().sessionsAfter('org-plain', null, 10)).to.deep.equal(['e1']);
    });
  });

  describe('markSessionsDeleted', () => {
    it("marks the org's named conversations deleted in the transaction and keeps an erasure already requested", async () => {
      const [org, other] = [oid(), oid()];
      const [fresh, requested, theirs] = [oid(), oid(), oid()];
      await insert('chatSessions', [
        { _id: new Types.ObjectId(fresh), orgId: new Types.ObjectId(org), isDeleted: false },
        { _id: new Types.ObjectId(requested), orgId: new Types.ObjectId(org), isDeleted: false, erasureRequestedAt: 5 },
        { _id: new Types.ObjectId(theirs), orgId: new Types.ObjectId(other), isDeleted: false },
      ]);
      const before = Date.now();
      await inMongoDurableTransaction((tx) =>
        new MongoOrgData().markSessionsDeleted(org, [fresh, requested, theirs, 'not-an-id'], tx),
      );
      const byId = async (id: string) => col('chatSessions').findOne({ _id: new Types.ObjectId(id) } as never);
      const marked = await byId(fresh);
      expect(marked?.['isDeleted']).to.equal(true);
      expect(marked?.['erasureRequestedAt']).to.be.at.least(before);
      expect((await byId(requested))?.['erasureRequestedAt']).to.equal(5);
      // Another org's conversation is never marked by this org's job, even when its id is named.
      expect((await byId(theirs))?.['isDeleted']).to.equal(false);
    });

    it('writes nothing when the transaction rolls back, for an org that is no ObjectId, or with no valid id', async () => {
      const org = oid();
      const session = oid();
      await insert('chatSessions', [{ _id: new Types.ObjectId(session), orgId: new Types.ObjectId(org), isDeleted: false }]);
      const data = new MongoOrgData();
      await inMongoDurableTransaction(async (tx) => {
        await data.markSessionsDeleted(org, [session], tx);
        throw new Error('rolled back');
      }).catch((error: Error) => expect(error.message).to.equal('rolled back'));
      await inMongoDurableTransaction((tx) => data.markSessionsDeleted('org-plain', [session], tx));
      await inMongoDurableTransaction((tx) => data.markSessionsDeleted(org, ['nope'], tx));
      expect(await count('chatSessions', { _id: new Types.ObjectId(session), isDeleted: false })).to.equal(1);
    });
  });

  it("pendingSessionErasures counts the org's pending session erasures only", async () => {
    const [org, other] = [oid(), oid()];
    await insert('agentSessionErasures', [
      { _id: randomUUID(), orgId: org, sessionId: 's1', status: 'pending' },
      { _id: randomUUID(), orgId: org, sessionId: 's2', status: 'pending' },
      { _id: randomUUID(), orgId: org, sessionId: 's3', status: 'done' },
      { _id: randomUUID(), orgId: other, sessionId: 's1', status: 'pending' },
    ]);
    expect(await new MongoOrgData().pendingSessionErasures(org)).to.equal(2);
  });

  it("activeRuns lists the org's runs that have not ended, as runs", async () => {
    const [org, other] = [oid(), oid()];
    const [queued, running] = [randomUUID(), randomUUID()];
    await insert('agentRuns', [
      { orgId: org, runId: queued, sessionId: 's1', status: 'queued', activeSessionKey: null },
      { orgId: org, runId: running, sessionId: 's2', status: 'running', activeSessionKey: null },
      { orgId: org, runId: randomUUID(), sessionId: 's3', status: 'completed' },
      { orgId: org, runId: randomUUID(), sessionId: 's4', status: 'cancelled' },
      { orgId: other, runId: randomUUID(), sessionId: 's5', status: 'running' },
    ]);
    const runs = await new MongoOrgData().activeRuns(org);
    expect(runs.map((run) => run.runId).sort()).to.deep.equal([queued, running].sort());
    // The stored bookkeeping is not part of a run.
    for (const run of runs) expect(run).not.to.have.property('activeSessionKey');
  });

  describe('eraseStores', () => {
    it("removes the org's documents however each store names its org, and counts them by store", async () => {
      const [org, other] = [oid(), oid()];
      const [mine, theirs] = [new Types.ObjectId(org), new Types.ObjectId(other)];
      await insert('chatSessions', [{ orgId: mine }, { orgId: mine }, { orgId: theirs }]);
      await insert('notifications', [{ orgId: mine }, { orgId: theirs }]);
      await insert('citations', [{ metadata: { orgId: org } }, { metadata: { orgId: other } }]);
      await insert('cpOrgHeads', [{ _id: org as never, revision: 1 }, { _id: other as never, revision: 1 }]);
      await insert('agentAdmissionQuotas', [
        { _id: `["actor","${org}","u1"]` as never },
        { _id: `["org","${org}"]` as never },
        { _id: `["org","${other}"]` as never },
      ]);
      await insert('outbox_events', [
        { topic: 'agent-run-events', value: JSON.stringify({ orgId: org, runId: 'r1' }) },
        { topic: 'agent-run-events', value: JSON.stringify({ orgId: other, runId: 'r2' }) },
        // The platform's own entity events are not the harness's to remove.
        { topic: 'entity-events', value: JSON.stringify({ orgId: org }) },
      ]);
      await insert('agentSandboxReaps', [{ orgId: org }, { orgId: other }]);
      // Each step erases its own: the audit chain, the data keys and a person's schedules are not this step's.
      await insert('agentAuditRecords', [{ orgId: org, seq: 1 }]);
      await insert('agentHarnessDataKeys', [{ _id: randomUUID() as never, orgId: org, version: 1 }]);
      await insert('agentSchedules', [{ _id: randomUUID() as never, orgId: org }]);

      const counts = await new MongoOrgData().eraseStores(org);

      expect(counts).to.deep.equal({
        chatSessions: 2,
        notifications: 1,
        citations: 1,
        cpOrgHeads: 1,
        agentAdmissionQuotas: 2,
        outbox_events: 1,
        agentSandboxReaps: 1,
      });
      expect(await count('chatSessions', { orgId: theirs })).to.equal(1);
      expect(await count('notifications', { orgId: theirs })).to.equal(1);
      expect(await count('citations', { 'metadata.orgId': other })).to.equal(1);
      expect(await count('cpOrgHeads', { _id: other })).to.equal(1);
      expect(await count('agentAdmissionQuotas', { _id: `["org","${other}"]` })).to.equal(1);
      expect(await count('outbox_events', { topic: 'entity-events' })).to.be.at.least(1);
      expect(await count('outbox_events', { value: { $regex: other } })).to.equal(1);
      expect(await count('agentSandboxReaps', { orgId: other })).to.equal(1);
      expect(await count('agentAuditRecords', { orgId: org })).to.equal(1);
      expect(await count('agentHarnessDataKeys', { orgId: org })).to.equal(1);
      expect(await count('agentSchedules', { orgId: org })).to.equal(1);
      // Run again, nothing is left to count.
      expect(await new MongoOrgData().eraseStores(org)).to.deep.equal({});
    });

    it('removes more than one batch, and a plan over several orgs keeps the other orgs', async () => {
      const [org, other] = [oid(), oid()];
      await insert('agentDispatchOutbox', Array.from({ length: 501 }, () => ({ orgId: org })));
      await insert('agentErasureBackfillPlans', [
        { _id: randomUUID() as never, orgFilter: org, orgs: [{ orgId: org }] },
        { _id: 'shared-plan' as never, orgFilter: null, orgs: [{ orgId: org, done: 1 }, { orgId: other, done: 2 }] },
      ]);
      const counts = await new MongoOrgData().eraseStores(org);
      expect(counts['agentDispatchOutbox']).to.equal(501);
      // One plan of the org removed, the shared one pulled.
      expect(counts['agentErasureBackfillPlans']).to.equal(2);
      const shared = await col('agentErasureBackfillPlans').findOne({ _id: 'shared-plan' } as never);
      expect(shared?.['orgs']).to.deep.equal([{ orgId: other, done: 2 }]);
    });

    it('an org id with regex characters matches only itself, and one that is no ObjectId reads no ObjectId store', async () => {
      const [dotted, lookalike] = ['o.1', 'ox1'];
      await insert('outbox_events', [
        { topic: 'agent-run-events', value: JSON.stringify({ orgId: dotted }) },
        { topic: 'agent-run-events', value: JSON.stringify({ orgId: lookalike }) },
      ]);
      await insert('agentAdmissionQuotas', [{ _id: `["org","${lookalike}"]` as never }]);
      await insert('agentSandboxReaps', [{ orgId: dotted }]);
      expect(await new MongoOrgData().eraseStores(dotted)).to.deep.equal({ outbox_events: 1, agentSandboxReaps: 1 });
      expect(await count('outbox_events', { value: { $regex: '"ox1"' } })).to.equal(1);
      expect(await count('agentAdmissionQuotas', { _id: `["org","${lookalike}"]` })).to.equal(1);
    });
  });

  describe('destroyDataKeys', () => {
    it("removes the org's stored keys when no key ring is given", async () => {
      const [org, other] = [oid(), oid()];
      await insert('agentHarnessDataKeys', [
        { _id: randomUUID() as never, orgId: org, version: 1 },
        { _id: randomUUID() as never, orgId: org, version: 2 },
        { _id: randomUUID() as never, orgId: other, version: 1 },
      ]);
      expect(await new MongoOrgData().destroyDataKeys(org)).to.equal(2);
      expect(await count('agentHarnessDataKeys', { orgId: org })).to.equal(0);
      expect(await count('agentHarnessDataKeys', { orgId: other })).to.equal(1);
    });

    it('shreds through the key ring, so its cache goes too, and touches no document itself', async () => {
      const org = oid();
      await insert('agentHarnessDataKeys', [{ _id: randomUUID() as never, orgId: org, version: 1 }]);
      const shredded: string[] = [];
      const data = new MongoOrgData({ shred: async (id) => (shredded.push(id), 3) });
      expect(await data.destroyDataKeys(org)).to.equal(3);
      expect(shredded).to.deep.equal([org]);
      expect(await count('agentHarnessDataKeys', { orgId: org })).to.equal(1);
    });
  });

  it("eraseAudit removes the org's records from every audit store, and no other org's", async () => {
    const [org, other] = [oid(), oid()];
    await insert('agentAuditRecords', [{ orgId: org, seq: 1 }, { orgId: org, seq: 2 }, { orgId: other, seq: 1 }]);
    await insert('agentAuditHeads', [{ orgId: org }]);
    await insert('agentAuditAnchors', [{ orgId: org }]);
    await insert('agentAuditPending', [{ orgId: other }]);
    // Not an audit store: left for the org stores step.
    await insert('agentSandboxReaps', [{ orgId: org }]);
    expect(await new MongoOrgData().eraseAudit(org)).to.equal(4);
    expect(await count('agentAuditRecords', { orgId: other })).to.equal(1);
    expect(await count('agentAuditPending', { orgId: other })).to.equal(1);
    expect(await count('agentSandboxReaps', { orgId: org })).to.equal(1);
  });

  it("OutboxOrgDeletedAnnouncer writes the platform's orgDeleted entity event, pending, ordered by the org", async () => {
    const org = oid();
    const before = Date.now();
    await new OutboxOrgDeletedAnnouncer().orgDeleted(org);
    const events = await OutboxEvent.find({ orderingKey: `org:${org}` }).lean();
    expect(events).to.have.length(1);
    const [event] = events;
    expect(event).to.include({ topic: 'entity-events', key: 'orgDeleted', status: 'pending', attempts: 0 });
    const value = JSON.parse(event!.value) as { eventType: string; timestamp: number; payload: unknown };
    expect(value.eventType).to.equal('orgDeleted');
    expect(value.payload).to.deep.equal({ orgId: org });
    expect(value.timestamp).to.be.at.least(before);
    expect((event!.headers as Record<string, string>)['timestamp']).to.equal(String(value.timestamp));
  });
});
