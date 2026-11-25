import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { deleteAgent } from '../../../src/modules/enterprise_search/controller/es_controller';
import { AIServiceCommand } from '../../../src/libs/commands/ai_service/ai.service.command';
import { InMemoryMemoryStore } from '../../../src/modules/agent-harness/adapters/in-memory-memory-store';
import { InMemoryAgentErasureQueue } from '../../../src/modules/agent-harness/adapters/in-memory-agent-erasures';
import { Logger } from '../../../src/libs/services/logger.service';
import { ServiceUnavailableError } from '../../../src/libs/errors/http.errors';
import { AGENT_DELETE_CONFIRM_AFTER_MS } from '../../../src/modules/agent-harness/domain/session-erasure';
import { MEMORY_ORG, memoryFixture } from '../agent-harness/support/memory-store-contract';

/**
 * H2 AM-2..AM-4 (P7.3 open item): deleting an agent erases its agent-scope
 * memories once the query service has deleted it, and only then. H4 AR-2..4
 * and H5 AE-1..AE-5: the erasure's intent is recorded before the delete.
 */

const ORG = MEMORY_ORG;
const appConfig = { aiBackend: 'http://localhost:8000' } as never;

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

const agentMemories = async (store: InMemoryMemoryStore, agentKey: string) =>
  (await store.list(ORG, { limit: 50 })).filter((m) => m.agentKey === agentKey);

// The controller logs through the logger it took at module load, which need not be today's `Logger.getInstance()`
// (another file in this mocha worker may have replaced the singleton): spy on the class, which every logger shares.
describe('H2 AM: deleting an agent erases its memories', () => {
  afterEach(() => sinon.restore());

  it('AM-2: after the query service deleted it, the agent\'s memories are erased, another agent\'s kept', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: { status: 'success' } } as any);
    const info = sinon.spy(Logger.prototype, 'info');
    const store = await seeded();
    const res = response();
    const next = sinon.stub();
    await deleteAgent(appConfig, store)(request(), res, next);
    expect(next.called).to.equal(false);
    expect(res.status.calledWith(200)).to.equal(true);
    expect(await agentMemories(store, 'agent-x')).to.deep.equal([]);
    expect(await agentMemories(store, 'agent-y')).to.have.length(1);
    const logged = info.getCalls().find((call) => call.args[0] === 'memory.agent_erased');
    expect(logged?.args[1]).to.include({ orgId: ORG, agentKey: 'agent-x', erased: 2 });
  });

  for (const statusCode of [403, 404, 500]) {
    it(`AM-3: the query service answers ${statusCode}: nothing is erased`, async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode, data: { detail: 'no' } } as any);
      const store = await seeded();
      const next = sinon.stub();
      await deleteAgent(appConfig, store)(request(), response(), next);
      expect(next.called).to.equal(true);
      expect(await agentMemories(store, 'agent-x')).to.have.length(2);
    });
  }

  it('AM-4: an erasure that fails after the agent was deleted still answers 200, warned once', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: {} } as any);
    const warn = sinon.spy(Logger.prototype, 'warn');
    const store = await seeded();
    sinon.stub(store, 'eraseAgent').rejects(new Error('mongo down'));
    const res = response();
    const next = sinon.stub();
    await deleteAgent(appConfig, store)(request(), res, next);
    expect(next.called).to.equal(false);
    expect(res.status.calledWith(200)).to.equal(true);
    const warned = warn.getCalls().filter((call) => call.args[0] === 'memory.agent_erase_failed');
    expect(warned).to.have.length(1);
    expect(warned[0]!.args[1]).to.include({ orgId: ORG, agentKey: 'agent-x', errorType: 'Error' });
  });

  it('AR-2 (H4): an erasure that fails after the delete is queued for the retention sweeper', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: {} } as any);
    const warn = sinon.spy(Logger.prototype, 'warn');
    const store = await seeded();
    sinon.stub(store, 'eraseAgent').rejects(new TypeError('mongo down'));
    const queue = new InMemoryAgentErasureQueue(() => 5);
    const res = response();
    await deleteAgent(appConfig, store, queue)(request(), res, sinon.stub<unknown[]>());
    expect(res.status.calledWith(200)).to.equal(true);
    const [entry] = await queue.due(5, 10);
    expect(entry).to.include({ orgId: ORG, agentKey: 'agent-x', lastErrorType: 'TypeError' });
    const warned = warn.getCalls().filter((call) => call.args[0] === 'memory.agent_erase_failed');
    expect(warned).to.have.length(1);
    expect(warned[0]!.args[1]).to.include({ choice: 'retry_queued' });
  });

  it('AR-3 (H4, H5): the erasure and the enqueue both fail: still 200, the intent left for the sweeper to confirm', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: {} } as any);
    const warn = sinon.spy(Logger.prototype, 'warn');
    const store = await seeded();
    sinon.stub(store, 'eraseAgent').rejects(new Error('mongo down'));
    const queue = new InMemoryAgentErasureQueue();
    sinon.stub(queue, 'enqueue').rejects(new Error('mongo down'));
    const res = response();
    const next = sinon.stub();
    await deleteAgent(appConfig, store, queue)(request(), res, next);
    expect(next.called).to.equal(false);
    expect(res.status.calledWith(200)).to.equal(true);
    const warned = warn.getCalls().filter((call) => call.args[0] === 'memory.agent_erase_failed');
    expect(warned).to.have.length(1);
    expect(warned[0]!.args[1]).to.include({ choice: 'confirm_by_lookup' });
    const [intent] = await queue.due(Number.MAX_SAFE_INTEGER, 10);
    expect(intent).to.include({ orgId: ORG, agentKey: 'agent-x', confirmed: false });
  });

  it('AR-3 (H4): without a queue composed, a failed erasure is left for an admin', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: {} } as any);
    const warn = sinon.spy(Logger.prototype, 'warn');
    const store = await seeded();
    sinon.stub(store, 'eraseAgent').rejects(new Error('mongo down'));
    await deleteAgent(appConfig, store)(request(), response(), sinon.stub<unknown[]>());
    const warned = warn.getCalls().filter((call) => call.args[0] === 'memory.agent_erase_failed');
    expect(warned[0]!.args[1]).to.include({ choice: 'left_for_admin' });
  });

  it('AE-1 (H5): the intent is recorded before the query service is asked to delete', async () => {
    const queue = new InMemoryAgentErasureQueue(() => 7);
    const seen: unknown[] = [];
    sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(async () => {
      seen.push(...(await queue.due(Number.MAX_SAFE_INTEGER, 10)));
      return { statusCode: 200, data: {} } as any;
    });
    await deleteAgent(appConfig, await seeded(), queue)(request(), response(), sinon.stub<unknown[]>());
    expect(seen).to.deep.equal([
      {
        orgId: ORG,
        agentKey: 'agent-x',
        confirmed: false,
        failures: 0,
        requestedAt: 7,
        nextAttemptAt: 7 + AGENT_DELETE_CONFIRM_AFTER_MS,
        lastErrorType: null,
      },
    ]);
  });

  it('AE-2 (H5): an intent that cannot be recorded refuses the delete (503); the query service is never asked', async () => {
    const execute = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: {} } as any);
    const warn = sinon.spy(Logger.prototype, 'warn');
    const store = await seeded();
    const queue = new InMemoryAgentErasureQueue();
    sinon.stub(queue, 'intend').rejects(new TypeError('mongo down'));
    const res = response();
    const next = sinon.stub();
    await deleteAgent(appConfig, store, queue)(request(), res, next);
    expect(execute.called).to.equal(false);
    expect(res.status.called).to.equal(false);
    const [error] = next.firstCall.args as [ServiceUnavailableError];
    expect(error).to.be.instanceOf(ServiceUnavailableError);
    expect(error.statusCode).to.equal(503);
    expect(error.metadata).to.include({ reasonCode: 'agent_erasure_unrecorded' });
    expect(await agentMemories(store, 'agent-x')).to.have.length(2);
    const warned = warn.getCalls().filter((call) => call.args[0] === 'memory.agent_erase_unrecorded');
    expect(warned).to.have.length(1);
    expect(warned[0]!.args[1]).to.include({ orgId: ORG, agentKey: 'agent-x', errorType: 'TypeError' });
  });

  it('AR-4 (H4), AE-3 (H5): an erasure that succeeds settles the intent', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: {} } as any);
    const queue = new InMemoryAgentErasureQueue();
    const store = await seeded();
    await deleteAgent(appConfig, store, queue)(request(), response(), sinon.stub<unknown[]>());
    expect(await agentMemories(store, 'agent-x')).to.deep.equal([]);
    expect(await queue.due(Number.MAX_SAFE_INTEGER, 10)).to.deep.equal([]);
  });

  for (const statusCode of [403, 404, 500]) {
    it(`AR-4 (H4), AE-5 (H5): a ${statusCode} refusal erases nothing and leaves only the unconfirmed intent`, async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode, data: {} } as any);
      const queue = new InMemoryAgentErasureQueue();
      const store = await seeded();
      await deleteAgent(appConfig, store, queue)(request(), response(), sinon.stub<unknown[]>());
      expect(await agentMemories(store, 'agent-x')).to.have.length(2);
      const due = await queue.due(Number.MAX_SAFE_INTEGER, 10);
      expect(due.map((entry) => [entry.agentKey, entry.confirmed])).to.deep.equal([['agent-x', false]]);
    });
  }
});
