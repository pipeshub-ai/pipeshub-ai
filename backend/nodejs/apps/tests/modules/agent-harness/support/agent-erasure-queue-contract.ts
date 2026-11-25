/**
 * What every `AgentErasureQueue` must do (H4 AR-1, H5 AE-14): the in-memory queue
 * (`agent-erasure-queue.test.ts`) and MongoDB (`agent-erasure-queue.mongo.itest.ts`).
 * `now` reads the queue's own clock (DB time for Mongo).
 */
import { expect } from 'chai';
import { AgentErasureQueue } from '../../../../src/modules/agent-harness/ports/agent-erasures';

/** Org ids in the shape every backend takes (PostgreSQL's org tables check it). */
const ORG_1 = 'a1a1a1a1a1a1a1a1a1a1a1a1';
const ORG_2 = 'b2b2b2b2b2b2b2b2b2b2b2b2';

export function describeAgentErasureQueueContract(
  name: string,
  create: () => Promise<{ queue: AgentErasureQueue; now: () => Promise<number> }>,
): void {
  describe(`AgentErasureQueue contract: ${name}`, () => {
    it('AR-1: enqueueing one agent twice keeps one entry, due at once, with its first time and failure', async () => {
      const { queue, now } = await create();
      await queue.enqueue(ORG_1, 'agent-x', 'MongoServerError');
      const [first] = await queue.due(await now(), 10);
      await queue.enqueue(ORG_1, 'agent-x', 'OtherError');
      const due = await queue.due(await now(), 10);
      expect(due).to.have.length(1);
      expect(due[0]).to.deep.equal(first);
      expect(due[0]).to.include({ orgId: ORG_1, agentKey: 'agent-x', failures: 1, lastErrorType: 'MongoServerError' });
    });

    it('AR-1: due answers what is due, oldest due first, at most the limit', async () => {
      const { queue, now } = await create();
      await queue.enqueue(ORG_1, 'a', 'E');
      await queue.enqueue(ORG_1, 'b', 'E');
      await queue.enqueue(ORG_1, 'c', 'E');
      const t = await now();
      const [a, b] = await queue.due(t, 10).then((d) => d.sort((x, y) => x.agentKey.localeCompare(y.agentKey)));
      await queue.retryLater(a!, t + 60_000, 'E');
      await queue.retryLater(b!, t + 30_000, 'E');
      expect((await queue.due(t, 10)).map((e) => e.agentKey)).to.deep.equal(['c']);
      expect((await queue.due(t + 60_000, 10)).map((e) => e.agentKey)).to.deep.equal(['c', 'b', 'a']);
      expect(await queue.due(t + 60_000, 2)).to.have.length(2);
    });

    it('AR-1: retryLater counts the failure and keeps the entry; settle removes it', async () => {
      const { queue, now } = await create();
      await queue.enqueue(ORG_1, 'agent-x', 'E1');
      const t = await now();
      const [entry] = await queue.due(t, 10);
      await queue.retryLater(entry!, t + 5_000, 'E2');
      const [again] = await queue.due(t + 5_000, 10);
      expect(again).to.include({ failures: 2, nextAttemptAt: t + 5_000, lastErrorType: 'E2' });
      await queue.settle(ORG_1, 'agent-x');
      expect(await queue.due(t + 5_000, 10)).to.deep.equal([]);
    });

    it('AE-14: an intent is unconfirmed, without a failure, due only after the delay', async () => {
      const { queue, now } = await create();
      await queue.intend(ORG_1, 'agent-x', 600_000);
      const t = await now();
      expect(await queue.due(t, 10)).to.deep.equal([]);
      const [entry] = await queue.due(Number.MAX_SAFE_INTEGER, 10);
      expect(entry).to.include({ orgId: ORG_1, agentKey: 'agent-x', confirmed: false, failures: 0, lastErrorType: null });
      expect(entry!.nextAttemptAt - entry!.requestedAt).to.equal(600_000);
      expect(entry!.requestedAt).to.be.at.most(t);
    });

    it('AE-14: a second intent requests the unconfirmed entry again from now', async () => {
      const { queue } = await create();
      await queue.intend(ORG_1, 'agent-x', 60_000);
      const [first] = await queue.due(Number.MAX_SAFE_INTEGER, 10);
      await queue.intend(ORG_1, 'agent-x', 120_000);
      const due = await queue.due(Number.MAX_SAFE_INTEGER, 10);
      expect(due).to.have.length(1);
      expect(due[0]!.requestedAt).to.be.at.least(first!.requestedAt);
      expect(due[0]!.nextAttemptAt - due[0]!.requestedAt).to.equal(120_000);
      expect(due[0]).to.include({ confirmed: false, failures: 0 });
    });

    it('AE-14: enqueue confirms an intent, due at once, keeping when it was requested', async () => {
      const { queue, now } = await create();
      await queue.intend(ORG_1, 'agent-x', 600_000);
      const [intent] = await queue.due(Number.MAX_SAFE_INTEGER, 10);
      await queue.enqueue(ORG_1, 'agent-x', 'MongoServerError');
      const [entry] = await queue.due(await now(), 10);
      expect(entry).to.include({
        confirmed: true,
        failures: 1,
        lastErrorType: 'MongoServerError',
        requestedAt: intent!.requestedAt,
      });
    });

    it('AE-14: an intent leaves a confirmed entry as it is', async () => {
      const { queue, now } = await create();
      await queue.enqueue(ORG_1, 'agent-x', 'E');
      const [confirmed] = await queue.due(await now(), 10);
      await queue.intend(ORG_1, 'agent-x', 600_000);
      expect(await queue.due(await now(), 10)).to.deep.equal([confirmed]);
    });

    it('AE-14: postpone moves the due time without counting a failure', async () => {
      const { queue } = await create();
      await queue.intend(ORG_1, 'agent-x', 1_000);
      const [entry] = await queue.due(Number.MAX_SAFE_INTEGER, 10);
      await queue.postpone(entry!, entry!.nextAttemptAt + 50_000);
      const [after] = await queue.due(Number.MAX_SAFE_INTEGER, 10);
      expect(after).to.deep.equal({ ...entry, nextAttemptAt: entry!.nextAttemptAt + 50_000 });
    });

    it('AE-14: withdraw removes the intent it read, never a newer request or a confirmed entry', async () => {
      const { queue, now } = await create();
      await queue.intend(ORG_1, 'agent-x', 1_000);
      const [entry] = await queue.due(Number.MAX_SAFE_INTEGER, 10);
      expect(await queue.withdraw({ ...entry!, requestedAt: entry!.requestedAt - 1 })).to.equal(false);
      await queue.enqueue(ORG_1, 'agent-x', 'E');
      expect(await queue.withdraw(entry!)).to.equal(false);
      expect(await queue.due(await now(), 10)).to.have.length(1);
      await queue.intend(ORG_1, 'agent-y', 1_000);
      const [other] = (await queue.due(Number.MAX_SAFE_INTEGER, 10)).filter((e) => e.agentKey === 'agent-y');
      expect(await queue.withdraw(other!)).to.equal(true);
      expect((await queue.due(Number.MAX_SAFE_INTEGER, 10)).map((e) => e.agentKey)).to.deep.equal(['agent-x']);
    });

    it("AR-1: another org's agent with the same key is its own entry", async () => {
      const { queue, now } = await create();
      await queue.enqueue(ORG_1, 'agent-x', 'E');
      await queue.enqueue(ORG_2, 'agent-x', 'E');
      await queue.settle(ORG_1, 'agent-x');
      expect((await queue.due(await now(), 10)).map((e) => e.orgId)).to.deep.equal([ORG_2]);
    });
  });
}
