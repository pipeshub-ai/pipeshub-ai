import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import {
  InMemoryAgentDeletionLookup,
  InMemoryAgentErasureQueue,
} from '../../../src/modules/agent-harness/adapters/in-memory-agent-erasures';
import { InMemoryMemoryStore } from '../../../src/modules/agent-harness/adapters/in-memory-memory-store';
import {
  AGENT_DELETE_CONFIRM_AFTER_MS,
  ERASURE_ALERT_AFTER_FAILURES,
  erasureBackoffMs,
} from '../../../src/modules/agent-harness/domain/session-erasure';
import { MEMORY_ORG, memoryFixture } from './support/memory-store-contract';
import { sweeperWithAgentErasures } from './support/agent-erasure-sweeper';

/**
 * H4 AR-5..AR-8: an agent erasure that failed at deletion is retried by the
 * retention sweeper until it is done, with the erasure backoff, never given up.
 */

const ORG = MEMORY_ORG;
const NOW = 1_000_000;

async function seeded(): Promise<InMemoryMemoryStore> {
  const store = new InMemoryMemoryStore();
  await store.propose(memoryFixture({ scope: 'agent', agentKey: 'agent-x' }));
  await store.propose(memoryFixture({ scope: 'agent', agentKey: 'agent-x', status: 'proposed' }));
  await store.propose(memoryFixture({ scope: 'agent', agentKey: 'agent-y' }));
  return store;
}

const keys = async (store: InMemoryMemoryStore) =>
  (await store.list(ORG, { limit: 50 })).map((m) => m.agentKey);

describe('H4 AR: a failed agent erasure is retried by the retention sweeper', () => {
  it("AR-5: a due entry is erased and settled; another agent's memories kept", async () => {
    const queue = new InMemoryAgentErasureQueue(() => NOW);
    const memories = await seeded();
    await queue.enqueue(ORG, 'agent-x', 'Error');
    const { sweeper, logged } = sweeperWithAgentErasures({ queue, memories, now: NOW });
    const report = await sweeper.tick();
    expect(report?.agentErasures).to.equal(1);
    expect(await keys(memories)).to.deep.equal(['agent-y']);
    expect(await queue.due(NOW, 10)).to.deep.equal([]);
    const line = logged.find((l) => l.message === 'agent_erasure.retried');
    expect(line?.meta).to.include({ orgId: ORG, agentKey: 'agent-x', erased: 2, failures: 1 });
  });

  it('AR-6: a failing retry stays, due again after the backoff; error from the alert threshold on, still retried', async () => {
    const queue = new InMemoryAgentErasureQueue(() => NOW);
    const memories = await seeded();
    const erase = sinon.stub(memories, 'eraseAgent').rejects(new TypeError('mongo down'));
    await queue.enqueue(ORG, 'agent-x', 'Error');
    let now = NOW;
    const levels: string[] = [];
    for (let attempt = 1; attempt <= ERASURE_ALERT_AFTER_FAILURES + 2; attempt += 1) {
      const { sweeper, logged } = sweeperWithAgentErasures({ queue, memories, now });
      expect((await sweeper.tick())?.agentErasures).to.equal(0);
      const [entry] = await queue.due(Number.MAX_SAFE_INTEGER, 10);
      expect(entry?.failures).to.equal(attempt + 1);
      expect(entry?.nextAttemptAt).to.equal(now + erasureBackoffMs(attempt + 1));
      expect(await queue.due(entry!.nextAttemptAt - 1, 10)).to.deep.equal([]);
      const line = logged.find((l) => l.message === 'agent_erasure.retry_failed')!;
      expect(line.meta).to.include({ orgId: ORG, agentKey: 'agent-x', errorType: 'TypeError' });
      levels.push(line.level);
      now = entry!.nextAttemptAt;
    }
    expect(levels.slice(0, ERASURE_ALERT_AFTER_FAILURES - 2)).to.satisfy((l: string[]) => l.every((x) => x === 'warn'));
    expect(levels[levels.length - 1]).to.equal('error');
    erase.restore();
    const { sweeper } = sweeperWithAgentErasures({ queue, memories, now });
    expect((await sweeper.tick())?.agentErasures).to.equal(1);
    expect(await keys(memories)).to.deep.equal(['agent-y']);
  });

  it('AR-7: idempotent: memories already gone settle with 0; two enqueues erase once', async () => {
    const queue = new InMemoryAgentErasureQueue(() => NOW);
    const memories = await seeded();
    await memories.eraseAgent(ORG, 'agent-x');
    await queue.enqueue(ORG, 'agent-x', 'Error');
    await queue.enqueue(ORG, 'agent-x', 'Error');
    const erase = sinon.spy(memories, 'eraseAgent');
    const { sweeper, logged } = sweeperWithAgentErasures({ queue, memories, now: NOW });
    expect((await sweeper.tick())?.agentErasures).to.equal(1);
    expect(erase.callCount).to.equal(1);
    expect(logged.find((l) => l.message === 'agent_erasure.retried')?.meta).to.include({ erased: 0 });
    expect((await sweeper.tick())?.agentErasures).to.equal(0);
  });

  it('AE-12 (H5): an intent whose lookup fails is retried after the backoff, alerting from the threshold, never erased', async () => {
    const queue = new InMemoryAgentErasureQueue(() => NOW);
    const memories = await seeded();
    await queue.intend(ORG, 'agent-x', 0);
    const deletions = new InMemoryAgentDeletionLookup();
    sinon.stub(deletions, 'state').rejects(new TypeError('query service down'));
    const erase = sinon.spy(memories, 'eraseAgent');
    let now = NOW;
    const levels: string[] = [];
    for (let attempt = 1; attempt <= ERASURE_ALERT_AFTER_FAILURES; attempt += 1) {
      const { sweeper, logged } = sweeperWithAgentErasures({ queue, memories, deletions, now });
      expect((await sweeper.tick())?.agentErasures).to.equal(0);
      const [entry] = await queue.due(Number.MAX_SAFE_INTEGER, 10);
      expect(entry).to.include({ confirmed: false, failures: attempt, lastErrorType: 'TypeError' });
      expect(entry!.nextAttemptAt).to.equal(now + erasureBackoffMs(attempt));
      levels.push(logged.find((l) => l.message === 'agent_erasure.retry_failed')!.level);
      now = entry!.nextAttemptAt;
    }
    expect(erase.called).to.equal(false);
    expect(levels.slice(0, -1).every((level) => level === 'warn')).to.equal(true);
    expect(levels[levels.length - 1]).to.equal('error');
    expect((await keys(memories)).sort()).to.deep.equal(['agent-x', 'agent-x', 'agent-y']);
  });

  it('AE-13 (H5): a live agent before the horizon postpones the intent without counting a failure', async () => {
    const queue = new InMemoryAgentErasureQueue(() => NOW);
    const memories = await seeded();
    await queue.intend(ORG, 'agent-x', 0);
    const deletions = new InMemoryAgentDeletionLookup();
    deletions.set(ORG, 'agent-x', 'live');
    const { sweeper, logged } = sweeperWithAgentErasures({ queue, memories, deletions, now: NOW });
    expect((await sweeper.tick())?.agentErasures).to.equal(0);
    const [entry] = await queue.due(Number.MAX_SAFE_INTEGER, 10);
    expect(entry).to.include({ confirmed: false, failures: 0, nextAttemptAt: NOW + AGENT_DELETE_CONFIRM_AFTER_MS });
    const line = logged.find((l) => l.message === 'agent_erasure.unconfirmed');
    expect(line?.level).to.equal('info');
    expect(line?.meta).to.include({ orgId: ORG, agentKey: 'agent-x', state: 'live' });
    expect(logged.some((l) => l.level === 'warn' || l.level === 'error')).to.equal(false);
  });

  it('AR-8: an unreadable queue fails its own part only', async () => {
    const queue = new InMemoryAgentErasureQueue(() => NOW);
    sinon.stub(queue, 'due').rejects(new Error('down'));
    const { sweeper, logged } = sweeperWithAgentErasures({ queue, memories: await seeded(), now: NOW });
    const report = await sweeper.tick();
    expect(report).to.include({ agentErasures: 0, payloads: 0 });
    expect(logged.find((l) => l.message === 'retention.part_failed')?.meta).to.include({ part: 'agent_erasures' });
  });
});
