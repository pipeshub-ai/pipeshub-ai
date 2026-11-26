/**
 * QA15-7 (ERA-21; H2, H4, H5 by behaviour): deleting an agent through the
 * production `deleteAgent` handler, against a query service on a loopback
 * port and real Mongo stores (`agentMemories`, `agentErasures`), then the
 * retention sweeper's agent-erasure part with the production deletion lookup.
 * Afterwards every collection is scanned for the agent's memories (their ids
 * and a canary in their content): an agent's own memories leave no trace
 * once it is deleted; its users' memories, and another agent's, stay; a
 * refused delete erases nothing. Run as execution-store.mongo.itest.ts says.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose, { Model } from 'mongoose';
import sinon from 'sinon';
import { randomUUID } from 'node:crypto';
import { createServer, Server } from 'node:http';
import { AddressInfo } from 'node:net';
import { registerUpstreams } from '../../../src/libs/http/upstream-call';
import { registerAppUpstreams } from '../../../src/modules/tokens_manager/config/config';
import { deleteAgent } from '../../../src/modules/enterprise_search/controller/es_controller';
import { HarnessAgentDeletionLookup } from '../../../src/modules/enterprise_search/adapters/harness-agent-deletions';
import { AuthTokenService } from '../../../src/libs/services/authtoken.service';
import { TokenScopes } from '../../../src/libs/enums/token-scopes.enum';
import { AgentErasureModel } from '../../../src/libs/repositories/agent-harness/agent-erasures/schema/agent-erasures.schema';
import { MongoAgentErasureQueue } from '../../../src/libs/repositories/agent-harness/agent-erasures/mongo';
import { AgentMemoryModel } from '../../../src/libs/repositories/agent-harness/memory/schema/memory.schema';
import { MongoMemoryStore } from '../../../src/libs/repositories/agent-harness/memory/mongo';
import { MongoScheduleStore } from '../../../src/libs/repositories/agent-harness/schedules/mongo';
import { AgentScheduleCounterModel, AgentScheduleModel } from '../../../src/libs/repositories/agent-harness/schedules/schema/schedules.schema';
import { agentDataEraser } from '../../../src/modules/agent-harness/binders/erasure';
import type { AgentDataEraser } from '../../../src/modules/agent-harness/ports/agent-erasures';
import { Container } from 'inversify';
import { aSchedule } from './support/schedules';
import { fixedMongoTime } from '../../../src/libs/repositories/agent-harness/time/mongo';
import {
  AGENT_DELETE_CONFIRM_AFTER_MS,
  AGENT_DELETE_WITHDRAW_AFTER_MS,
} from '../../../src/modules/agent-harness/domain/session-erasure';
import { MemoryEntry } from '../../../src/modules/agent-harness/domain/memory';
import { memoryFixture } from './support/memory-store-contract';
import { sweeperWithAgentErasures } from './support/agent-erasure-sweeper';
import { describeLeak, judgeCensus, needlesOf, scanMongo } from './support/erasure-census';
import { connectHarnessMongo, disconnectHarnessMongo, registerHarnessModels } from './support/harness-mongo';

registerHarnessModels([AgentErasureModel, AgentMemoryModel, AgentScheduleModel, AgentScheduleCounterModel] as unknown as Array<Model<never>>);

const SECRETS = { jwtSecret: 'qa15-jwt-secret-0123456789abcdef', scopedJwtSecret: 'qa15-scoped-secret-0123456789abcd' };
const T0 = 1_000_000;

type DeleteAnswer = 'deleted' | 'failed' | 'committed_then_500' | 'refused';

/**
 * The query service's two agent routes as Node calls them: the delete (answers
 * as told, and records what it committed) and the H5 tombstone lookup, which
 * refuses a token without the `agent:erase` scope.
 */
class FakeQueryService {
  answer: DeleteAnswer = 'deleted';
  readonly deleted = new Set<string>();
  private server: Server | null = null;
  private readonly tokens = new AuthTokenService(SECRETS.jwtSecret, SECRETS.scopedJwtSecret);

  async start(): Promise<string> {
    this.server = createServer((request, response) => {
      const reply = (status: number, body: unknown): void => {
        response.writeHead(status, { 'Content-Type': 'application/json' });
        response.end(JSON.stringify(body));
      };
      const match = /^\/api\/v1\/agent\/([^/]+)(\/internal\/deletion)?$/.exec(request.url ?? '');
      if (match === null) return reply(404, { detail: 'not found' });
      const agentKey = decodeURIComponent(match[1]!);
      if (request.method === 'GET' && match[2] !== undefined) {
        const token = (request.headers.authorization ?? '').replace(/^Bearer /, '');
        void this.tokens
          .verifyScopedToken(token, TokenScopes.AGENT_ERASE)
          .then(() => reply(200, { state: this.deleted.has(agentKey) ? 'deleted' : 'live' }))
          .catch(() => reply(401, { detail: 'unauthorized' }));
        return;
      }
      if (request.method !== 'DELETE' || match[2] !== undefined) return reply(405, { detail: 'method' });
      if (this.answer === 'refused') return reply(404, { detail: 'Agent not found' });
      if (this.answer === 'failed') return reply(500, { detail: 'query service failed' });
      this.deleted.add(agentKey);
      return this.answer === 'deleted' ? reply(200, { agentKey }) : reply(500, { detail: 'answer lost' });
    });
    await new Promise<void>((resolve) => this.server!.listen(0, '127.0.0.1', resolve));
    return `http://127.0.0.1:${String((this.server!.address() as AddressInfo).port)}`;
  }

  async stop(): Promise<void> {
    await new Promise<void>((resolve) => (this.server ? this.server.close(() => resolve()) : resolve()));
  }
}

interface Agents {
  orgId: string;
  agentKey: string;
  /** The deleted agent's own memories (scope `agent`). */
  own: MemoryEntry[];
  /** Memories that are not the agent's: a user's in a conversation with it, another agent's. */
  others: MemoryEntry[];
  canary: string;
  otherCanary: string;
}

async function seedAgents(): Promise<Agents> {
  const orgId = `org-${randomUUID()}`;
  const agentKey = `agent-${randomUUID()}`;
  const canary = `SENTINEL-agent-memory:${randomUUID()}`;
  const otherCanary = `SENTINEL-kept-memory:${randomUUID()}`;
  const store = new MongoMemoryStore();
  const own = [
    memoryFixture({ orgId, scope: 'agent', agentKey, ownerId: 'admin', content: canary }),
    memoryFixture({ orgId, scope: 'agent', agentKey, ownerId: 'admin', content: canary, status: 'proposed' }),
  ];
  const others = [
    memoryFixture({ orgId, scope: 'user', agentKey, ownerId: 'alice', content: otherCanary }),
    memoryFixture({ orgId, scope: 'agent', agentKey: `agent-${randomUUID()}`, ownerId: 'admin', content: otherCanary }),
  ];
  for (const entry of [...own, ...others]) await store.propose(entry);
  return { orgId, agentKey, own, others, canary, otherCanary };
}

const ownNeedles = (a: Agents) => needlesOf(a.own.map((m) => `${a.orgId}:${m.memoryId}`), [a.canary]);

async function expectAgentErased(a: Agents): Promise<void> {
  const verdict = judgeCensus(await scanMongo(mongoose.connection.db!, ownNeedles(a)));
  expect(verdict.leaks.map(describeLeak), 'left behind').to.deep.equal([]);
  expect(await AgentMemoryModel.countDocuments({ orgId: a.orgId, content: a.otherCanary })).to.equal(2);
  expect(await AgentErasureModel.countDocuments({ orgId: a.orgId })).to.equal(0);
}

async function expectAgentIntact(a: Agents): Promise<void> {
  expect(await AgentMemoryModel.countDocuments({ orgId: a.orgId, content: a.canary })).to.equal(2);
  expect(await AgentMemoryModel.countDocuments({ orgId: a.orgId, content: a.otherCanary })).to.equal(2);
}

describe('QA15-7 agent erasure on a MongoDB replica set', function () {
  this.timeout(60_000);
  let query: FakeQueryService;
  let aiBackend: string;
  let now: number;
  const queue = () => new MongoAgentErasureQueue(fixedMongoTime(() => now));

  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(async () => {
    await AgentErasureModel.deleteMany({});
    await AgentMemoryModel.deleteMany({});
    query = new FakeQueryService();
    aiBackend = await query.start();
    // Service calls go only to registered PipesHub services (main #6192): the fake query service is the app's.
    registerAppUpstreams({ aiBackend });
    now = T0;
  });
  afterEach(async () => {
    sinon.restore();
    registerUpstreams({});
    await query.stop();
  });

  const remove = async (a: Agents, memories: AgentDataEraser = new MongoMemoryStore()): Promise<unknown> => {
    const next = sinon.stub();
    const res = { status: sinon.stub(), json: sinon.stub() };
    res.status.returns(res);
    await deleteAgent({ aiBackend } as never, memories, queue())(
      {
        headers: { authorization: 'Bearer user' },
        params: { agentKey: a.agentKey },
        user: { userId: 'admin', orgId: a.orgId },
        context: { requestId: 'req-qa15' },
      } as never,
      res as never,
      next,
    );
    return next.firstCall?.args[0];
  };

  const sweep = (at: number) => {
    now = at;
    return sweeperWithAgentErasures({
      queue: queue(),
      memories: new MongoMemoryStore(),
      deletions: new HarnessAgentDeletionLookup(() => ({ aiBackend, ...SECRETS })),
      now: at,
    }).sweeper.tick();
  };

  it('QA15-7a: a deleted agent leaves no memory of its own in any collection; its users\' and other agents\' memories stay', async () => {
    const a = await seedAgents();
    expect(await remove(a)).to.equal(undefined);
    await expectAgentErased(a);
  });

  it("CLEANUP-G3: deleting an agent through the composed eraser also disables its schedules and drops their templates", async () => {
    const a = await seedAgents();
    const schedules = new MongoScheduleStore();
    const its = await schedules.create(aSchedule({ orgId: a.orgId, agentKey: a.agentKey }), { perActor: 50, perOrg: 500 });
    const other = await schedules.create(aSchedule({ orgId: a.orgId, agentKey: `agent-${randomUUID()}` }), { perActor: 50, perOrg: 500 });
    expect(await remove(a, agentDataEraser(new Container()))).to.equal(undefined);
    await expectAgentErased(a);
    expect(await schedules.get(a.orgId, its.id)).to.deep.include({ status: 'disabled', statusReason: 'agent_deleted', input: null, nextFireAt: null });
    expect((await schedules.get(a.orgId, other.id))?.status).to.equal('active');
  });

  it('QA15-7b: the erasure fails after the delete committed: queued, and the next sweep erases it', async () => {
    const a = await seedAgents();
    const failing = new MongoMemoryStore();
    sinon.stub(failing, 'eraseAgent').rejects(new Error('mongo stepped down'));
    await remove(a, failing);
    await expectAgentIntact(a);
    expect((await sweep(T0))?.agentErasures).to.equal(1);
    await expectAgentErased(a);
  });

  it('QA15-7c: the query service committed the delete but its answer was lost: the sweeper confirms by lookup and erases', async () => {
    query.answer = 'committed_then_500';
    const a = await seedAgents();
    expect(await remove(a)).to.be.instanceOf(Error);
    await expectAgentIntact(a);
    expect((await sweep(T0 + AGENT_DELETE_CONFIRM_AFTER_MS - 1))?.agentErasures).to.equal(0);
    await expectAgentIntact(a);
    expect((await sweep(T0 + AGENT_DELETE_CONFIRM_AFTER_MS))?.agentErasures).to.equal(1);
    await expectAgentErased(a);
  });

  for (const answer of ['refused', 'failed'] as const) {
    it(`QA15-7d: a delete the query service ${answer}: nothing erased, the intent withdrawn at the horizon`, async () => {
      query.answer = answer;
      const a = await seedAgents();
      expect(await remove(a)).to.be.instanceOf(Error);
      for (let at = T0 + AGENT_DELETE_CONFIRM_AFTER_MS; at <= T0 + AGENT_DELETE_WITHDRAW_AFTER_MS; at += AGENT_DELETE_CONFIRM_AFTER_MS) {
        await sweep(at);
      }
      await expectAgentIntact(a);
      expect(await AgentErasureModel.countDocuments({ orgId: a.orgId })).to.equal(0);
    });
  }
});
