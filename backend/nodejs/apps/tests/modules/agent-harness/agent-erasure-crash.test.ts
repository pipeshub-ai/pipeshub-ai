import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { deleteAgent } from '../../../src/modules/enterprise_search/controller/es_controller';
import { AIServiceCommand } from '../../../src/libs/commands/ai_service/ai.service.command';
import { InMemoryMemoryStore } from '../../../src/modules/agent-harness/adapters/in-memory-memory-store';
import {
  InMemoryAgentDeletionLookup,
  InMemoryAgentErasureQueue,
} from '../../../src/modules/agent-harness/adapters/in-memory-agent-erasures';
import {
  AGENT_DELETE_CONFIRM_AFTER_MS,
  AGENT_DELETE_WITHDRAW_AFTER_MS,
} from '../../../src/modules/agent-harness/domain/session-erasure';
import { AgentDeletionState } from '../../../src/modules/agent-harness/ports/agent-erasures';
import { MEMORY_ORG, memoryFixture } from './support/memory-store-contract';
import { sweeperWithAgentErasures } from './support/agent-erasure-sweeper';

/**
 * H5 AE-6..AE-11: the agent-deletion request dies at each point, then the
 * retention sweeper runs. A death is a step that never returns: nothing after
 * it runs, and the stores keep what was written before it.
 */

const ORG = MEMORY_ORG;
const T0 = 1_000_000;
const appConfig = { aiBackend: 'http://localhost:8000' } as never;

const never = <T>(): Promise<T> => new Promise<T>(() => undefined);

function request(): any {
  return {
    headers: { authorization: 'Bearer t' },
    params: { agentKey: 'agent-x' },
    user: { userId: 'user-1', orgId: ORG },
    context: { requestId: 'req-1' },
  };
}

function response(): any {
  const res: any = { status: sinon.stub(), json: sinon.stub() };
  res.status.returns(res);
  return res;
}

async function seeded(): Promise<InMemoryMemoryStore> {
  const store = new InMemoryMemoryStore();
  await store.propose(memoryFixture({ scope: 'agent', agentKey: 'agent-x' }));
  await store.propose(memoryFixture({ scope: 'agent', agentKey: 'agent-x', status: 'proposed' }));
  await store.propose(memoryFixture({ scope: 'agent', agentKey: 'agent-y' }));
  return store;
}

const keys = async (store: InMemoryMemoryStore) =>
  (await store.list(ORG, { limit: 50 })).map((m) => m.agentKey).sort();

/** Starts the delete and lets it run until it blocks for good (the process died there). */
async function diesDuring(memories: InMemoryMemoryStore, queue: InMemoryAgentErasureQueue): Promise<void> {
  void deleteAgent(appConfig, memories, queue)(request(), response(), sinon.stub<unknown[]>());
  await new Promise((resolve) => setImmediate(resolve));
}

function lookup(state: AgentDeletionState): InMemoryAgentDeletionLookup {
  const deletions = new InMemoryAgentDeletionLookup();
  deletions.set(ORG, 'agent-x', state);
  return deletions;
}

async function sweepAt(
  now: number,
  queue: InMemoryAgentErasureQueue,
  memories: InMemoryMemoryStore,
  deletions: InMemoryAgentDeletionLookup,
) {
  return sweeperWithAgentErasures({ queue, memories, deletions, now }).sweeper.tick();
}

describe('H5 AE: an agent delete that dies at any point still erases exactly the deleted agent', () => {
  afterEach(() => sinon.restore());

  it('AE-6: died before the query service answered, the agent live: nothing erased, the intent withdrawn later', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').returns(never());
    const memories = await seeded();
    const queue = new InMemoryAgentErasureQueue(() => T0);
    await diesDuring(memories, queue);
    const deletions = lookup('live');
    const state = sinon.spy(deletions, 'state');

    expect((await sweepAt(T0 + AGENT_DELETE_CONFIRM_AFTER_MS - 1, queue, memories, deletions))?.agentErasures).to.equal(0);
    expect(state.called, 'looked up before the confirm delay').to.equal(false);

    expect((await sweepAt(T0 + AGENT_DELETE_CONFIRM_AFTER_MS, queue, memories, deletions))?.agentErasures).to.equal(0);
    expect(state.callCount).to.equal(1);
    expect(await queue.due(Number.MAX_SAFE_INTEGER, 10)).to.have.length(1);

    await sweepAt(T0 + AGENT_DELETE_WITHDRAW_AFTER_MS, queue, memories, deletions);
    expect(await queue.due(Number.MAX_SAFE_INTEGER, 10)).to.deep.equal([]);
    expect(await keys(memories)).to.deep.equal(['agent-x', 'agent-x', 'agent-y']);
  });

  it('AE-7: died after the query service committed the delete, before the 200 reached Node: erased by the sweeper', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').returns(never());
    const memories = await seeded();
    const queue = new InMemoryAgentErasureQueue(() => T0);
    await diesDuring(memories, queue);
    const { sweeper, logged } = sweeperWithAgentErasures({
      queue,
      memories,
      deletions: lookup('deleted'),
      now: T0 + AGENT_DELETE_CONFIRM_AFTER_MS,
    });
    expect((await sweeper.tick())?.agentErasures).to.equal(1);
    expect(await keys(memories)).to.deep.equal(['agent-y']);
    expect(await queue.due(Number.MAX_SAFE_INTEGER, 10)).to.deep.equal([]);
    expect(logged.find((l) => l.message === 'agent_erasure.retried')?.meta).to.include({
      orgId: ORG,
      agentKey: 'agent-x',
      erased: 2,
      confirmedBy: 'lookup',
    });
  });

  it('AE-7: died after the 200, before the erasure', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: {} } as any);
    const memories = await seeded();
    sinon.stub(memories, 'eraseAgent').returns(never());
    const queue = new InMemoryAgentErasureQueue(() => T0);
    await diesDuring(memories, queue);
    sinon.restore();
    expect(await keys(memories)).to.deep.equal(['agent-x', 'agent-x', 'agent-y']);
    await sweepAt(T0 + AGENT_DELETE_CONFIRM_AFTER_MS, queue, memories, lookup('deleted'));
    expect(await keys(memories)).to.deep.equal(['agent-y']);
    expect(await queue.due(Number.MAX_SAFE_INTEGER, 10)).to.deep.equal([]);
  });

  it('AE-8: died after the erasure, before the intent was settled: erased again (nothing left), settled', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: {} } as any);
    const memories = await seeded();
    const queue = new InMemoryAgentErasureQueue(() => T0);
    sinon.stub(queue, 'settle').returns(never());
    await diesDuring(memories, queue);
    sinon.restore();
    expect(await keys(memories)).to.deep.equal(['agent-y']);
    const { sweeper, logged } = sweeperWithAgentErasures({
      queue,
      memories,
      deletions: lookup('deleted'),
      now: T0 + AGENT_DELETE_CONFIRM_AFTER_MS,
    });
    expect((await sweeper.tick())?.agentErasures).to.equal(1);
    expect(logged.find((l) => l.message === 'agent_erasure.retried')?.meta).to.include({ erased: 0 });
    expect(await queue.due(Number.MAX_SAFE_INTEGER, 10)).to.deep.equal([]);
  });

  it('AE-9: died after a failed erasure was confirmed: the sweeper erases at once, without asking', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: {} } as any);
    const memories = await seeded();
    sinon.stub(memories, 'eraseAgent').rejects(new Error('mongo down'));
    const queue = new InMemoryAgentErasureQueue(() => T0);
    const enqueue = queue.enqueue.bind(queue);
    sinon.stub(queue, 'enqueue').callsFake(async (...args) => {
      await enqueue(...args);
      return never();
    });
    await diesDuring(memories, queue);
    sinon.restore();
    const deletions = new InMemoryAgentDeletionLookup();
    const state = sinon.spy(deletions, 'state');
    await sweepAt(T0, queue, memories, deletions);
    expect(state.called).to.equal(false);
    expect(await keys(memories)).to.deep.equal(['agent-y']);
  });

  it('AE-10: the query service answered 500 but its delete committed: erased by the sweeper', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 500, data: { detail: 'x' } } as any);
    const memories = await seeded();
    const queue = new InMemoryAgentErasureQueue(() => T0);
    const next = sinon.stub();
    await deleteAgent(appConfig, memories, queue)(request(), response(), next);
    expect(next.called).to.equal(true);
    expect(await keys(memories)).to.deep.equal(['agent-x', 'agent-x', 'agent-y']);
    await sweepAt(T0 + AGENT_DELETE_CONFIRM_AFTER_MS, queue, memories, lookup('deleted'));
    expect(await keys(memories)).to.deep.equal(['agent-y']);
  });

  for (const state of ['absent', 'live'] as const) {
    it(`AE-11: a refused delete (${state}): never erased, withdrawn at the horizon`, async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 404, data: { detail: 'x' } } as any);
      const memories = await seeded();
      const queue = new InMemoryAgentErasureQueue(() => T0);
      await deleteAgent(appConfig, memories, queue)(request(), response(), sinon.stub<unknown[]>());
      const deletions = lookup(state);
      const erase = sinon.spy(memories, 'eraseAgent');
      for (let now = T0 + AGENT_DELETE_CONFIRM_AFTER_MS; now <= T0 + AGENT_DELETE_WITHDRAW_AFTER_MS; now += AGENT_DELETE_CONFIRM_AFTER_MS) {
        await sweepAt(now, queue, memories, deletions);
      }
      expect(erase.called).to.equal(false);
      expect(await queue.due(Number.MAX_SAFE_INTEGER, 10)).to.deep.equal([]);
      expect(await keys(memories)).to.deep.equal(['agent-x', 'agent-x', 'agent-y']);
    });
  }
});
