/**
 * UX5 FU: suggested next questions after the run's terminal commit. The
 * worker's internal route (attempt token, validation, a completed run only,
 * the first write wins) over the real router, controller, ExecutionService
 * and in-memory store; and the owner's session route, which answers what the
 * run document holds or waits for it, woken by the activity bus's
 * `followups` frame or the store poll. Time is a manual timer, never a sleep.
 */
import 'reflect-metadata';
import express from 'express';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { expect } from 'chai';
import { Container } from 'inversify';
import sinon from 'sinon';
import { Logger } from '../../../src/libs/services/logger.service';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { InMemoryConfigDocumentStore } from '../../../src/modules/agent-harness/adapters/in-memory-config-document-store';
import { InMemoryLiveBus } from '../../../src/modules/agent-harness/adapters/in-memory-live-bus';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_RUN_FOLLOWUPS,
  AGENT_HARNESS_SESSION_RUN_SERVICE,
  createHarnessAuthority,
} from '../../../src/modules/agent-harness/composition';
import {
  AttemptKeyring,
  PublishedAttemptKeys,
  WorkerCredentialSet,
} from '../../../src/modules/agent-harness/domain/authority';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { SessionRunService } from '../../../src/modules/agent-harness/application/session-run-service';
import {
  FollowupTimers,
  RunFollowupsService,
} from '../../../src/modules/agent-harness/application/run-followups-service';
import { ChatSessionRepository } from '../../../src/modules/agent-harness/ports/chat-session-repository';
import {
  ACTIVITY_LIVE_CHANNEL,
  ActivityFrame,
} from '../../../src/modules/agent-harness/projection/activity';
import {
  AGENT_HARNESS_INTERNAL_PREFIX,
  createAgentHarnessInternalRouter,
} from '../../../src/modules/agent-harness/transport/internal-routes';
import {
  createAgentHarnessSessionRouter,
  createChatHarnessSessionRouter,
} from '../../../src/modules/agent-harness/transport/session-routes';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import {
  contractClock,
  ContractHarness,
  createContractHarness,
  DURABLE_CAPABILITIES,
} from './support/execution-store-contract';
import { testCipher } from './support/data-protection';

const LEASE_MS = 2_000;

interface Reply {
  status: number;
  body: Record<string, any>;
}

const listen = async (app: express.Express): Promise<{ server: Server; base: string }> => {
  const server = await new Promise<Server>((resolve) => {
    const listening = app.listen(0, () => resolve(listening));
  });
  return { server, base: `http://127.0.0.1:${String((server.address() as AddressInfo).port)}` };
};

const closeServer = (server: Server): Promise<void> =>
  new Promise((resolve) => {
    server.closeAllConnections();
    server.close(() => resolve());
  });

/** Lets settled promises and I/O callbacks run; it does not wait for time. */
const turn = (): Promise<void> => new Promise((resolve) => setImmediate(resolve));

const until = async (condition: () => boolean): Promise<void> => {
  for (let i = 0; i < 1_000 && !condition(); i += 1) await turn();
  expect(condition(), 'condition never held').to.equal(true);
};

describe('agent-harness follow-ups: the worker\'s internal route (UX5)', function () {
  this.timeout(20_000);
  let server: Server;
  let base: string;
  let h: ContractHarness;
  let authority: ReturnType<typeof createHarnessAuthority>;
  let credential: string;
  let info: sinon.SinonStub;

  beforeEach(async () => {
    const storeClock = contractClock();
    const store = new InMemoryExecutionStore(storeClock);
    const payloads = new InMemoryPayloadStore();
    h = createContractHarness(
      {
        executionStore: store,
        journal: store.journal,
        payloads,
        admissionStore: store,
        dispatches: async () => [],
        deletePayload: async () => undefined,
      },
      storeClock,
    );
    const now = Date.UTC(2026, 9, 10);
    const stores = {
      workerCredential: new InMemoryConfigDocumentStore<WorkerCredentialSet>(),
      attemptSigningKeys: new InMemoryConfigDocumentStore<AttemptKeyring>(),
      attemptPublicKeys: new InMemoryConfigDocumentStore<PublishedAttemptKeys>(),
    };
    authority = createHarnessAuthority(stores, h.service, { now: () => now });
    await authority.provision(null);
    credential = (await stores.workerCredential.read()).value!.current;
    const logger = { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() };
    info = logger.info;
    // The router's service logs; the harness's own is silent.
    const logged = new ExecutionService({
      store,
      journal: store.journal,
      journals: new JournalService(payloads, testCipher()),
      capabilities: DURABLE_CAPABILITIES,
      logger,
    });
    const container = new Container();
    container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue(logged);
    container.bind(AGENT_HARNESS_AUTHORITY).toConstantValue(authority);
    container.bind('Logger').toConstantValue(logger);
    const app = express();
    app.use(express.json());
    app.use(AGENT_HARNESS_INTERNAL_PREFIX, createAgentHarnessInternalRouter(container));
    app.use(
      (
        error: { statusCode?: number; code?: string; message: string },
        _req: express.Request,
        res: express.Response,
        // eslint-disable-next-line @typescript-eslint/no-unused-vars -- Express treats only a four-argument handler as error middleware
        _next: express.NextFunction,
      ) => {
        res.status(error.statusCode ?? 500).json({ error: { code: error.code, message: error.message } });
      },
    );
    const listening = await listen(app);
    server = listening.server;
    base = `${listening.base}${AGENT_HARNESS_INTERNAL_PREFIX}`;
  });

  afterEach(async () => {
    await closeServer(server);
  });

  const post = async (path: string, bearer: string | null, body: unknown): Promise<Reply> => {
    const headers: Record<string, string> = { 'content-type': 'application/json' };
    if (bearer !== null) headers['authorization'] = `Bearer ${bearer}`;
    const response = await fetch(`${base}${path}`, { method: 'POST', headers, body: JSON.stringify(body) });
    return { status: response.status, body: (await response.json()) as Record<string, any> };
  };

  const claimed = async () => {
    const admitted = await h.admit();
    const reply = await post(`/runs/${admitted.run.runId}/claim`, credential, {
      orgId: admitted.run.orgId,
      workerId: 'worker-a',
      leaseDurationMs: LEASE_MS,
    });
    expect(reply.status, JSON.stringify(reply.body)).to.equal(200);
    return { run: reply.body['run'] as HarnessRun, token: reply.body['attemptToken']['token'] as string };
  };

  const completed = async () => {
    const { run, token } = await claimed();
    await h.service.commit({
      commandId: 'complete-1',
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'worker-a',
      attemptId: run.attemptId as string,
      fence: run.fence,
      expectedRevision: run.revision,
      status: 'completed',
      outcome: 'answer',
      events: [{ type: 'run_completed', data: { answer: 'done' } }],
      outbox: [],
    });
    return { run, token };
  };

  const offer = (run: HarnessRun, token: string | null, items: unknown, extra: Record<string, unknown> = {}) =>
    post(`/runs/${run.runId}/followups`, token, { attemptId: run.attemptId, fence: run.fence, items, ...extra });

  const stored = async (run: HarnessRun) => (await h.service.get(run.orgId, run.runId))?.followups;

  it('stores a completed run\'s follow-ups with its attempt token and logs the count, never the text', async () => {
    const { run, token } = await completed();
    const items = [{ text: 'Compare with last quarter' }, { text: 'Draft an email' }];
    const reply = await offer(run, token, items);
    expect(reply.status, JSON.stringify(reply.body)).to.equal(200);
    expect(reply.body).to.deep.equal({ followups: items });
    expect(await stored(run)).to.deep.equal(items);
    const lines = info.getCalls().filter((call) => call.args[0] === 'run.followups_stored');
    expect(lines).to.have.length(1);
    expect(lines[0]!.args[1]).to.deep.equal({ event: 'run.followups_stored', orgId: run.orgId, runId: run.runId, count: 2 });
  });

  it('the first write wins: a retry answers 200 with what was stored and writes nothing', async () => {
    const { run, token } = await completed();
    expect((await offer(run, token, [{ text: 'First' }])).status).to.equal(200);
    const retry = await offer(run, token, [{ text: 'Second' }]);
    expect(retry.status).to.equal(200);
    expect(retry.body).to.deep.equal({ followups: [{ text: 'First' }] });
    expect(await stored(run)).to.deep.equal([{ text: 'First' }]);
    expect(info.getCalls().filter((call) => call.args[0] === 'run.followups_stored')).to.have.length(1);
  });

  it('a run that has not completed: 409 RUN_NOT_COMPLETED, nothing stored', async () => {
    const { run, token } = await claimed();
    const reply = await offer(run, token, [{ text: 'Too early' }]);
    expect(reply.status).to.equal(409);
    expect(reply.body['error']['code']).to.equal('HTTP_AGENT_HARNESS_RUN_NOT_COMPLETED');
    expect(await stored(run)).to.equal(undefined);
  });

  it('refuses a missing token, the worker credential, another run\'s token and a body naming another attempt (401)', async () => {
    const { run, token } = await completed();
    const other = await claimed();
    const items = [{ text: 'Next?' }];
    expect((await offer(run, null, items)).status).to.equal(401);
    expect((await offer(run, credential, items)).status).to.equal(401);
    expect((await offer(run, other.token, items)).status).to.equal(401);
    expect((await offer(run, token, items, { attemptId: other.run.attemptId })).status).to.equal(401);
    expect((await offer(run, token, items, { fence: run.fence + 1 })).status).to.equal(401);
    expect(await stored(run)).to.equal(undefined);
  });

  it('a token for an unknown run, or for the same run id in another org: 404', async () => {
    const { run } = await completed();
    for (const target of [{ ...run, runId: 'no-such-run' }, { ...run, orgId: 'another-org' }]) {
      const forged = (await authority.issueFor(target, LEASE_MS)).token;
      const reply = await offer(target, forged, [{ text: 'Next?' }]);
      expect(reply.status, target.runId).to.equal(404);
    }
    expect(await stored(run)).to.equal(undefined);
  });

  it('validates the body: 1 to 3 items of 1 to 160 characters (code points), nothing else (400)', async () => {
    const { run, token } = await completed();
    const refused: Array<[string, unknown, Record<string, unknown>?]> = [
      ['no items', []],
      ['four items', [{ text: 'a' }, { text: 'b' }, { text: 'c' }, { text: 'd' }]],
      ['an empty text', [{ text: '' }]],
      ['161 characters', [{ text: 'x'.repeat(161) }]],
      ['an extra item field', [{ text: 'a', url: 'https://example.com' }]],
      ['not a list', 'Next?'],
      ['an extra body field', [{ text: 'a' }], { orgId: 'another-org' }],
    ];
    for (const [why, items, extra] of refused) {
      expect((await offer(run, token, items, extra)).status, why).to.equal(400);
    }
    expect(await stored(run)).to.equal(undefined);
    const wide = [{ text: '\u{1F600}'.repeat(160) }];
    expect((await offer(run, token, wide)).status, '160 code points, 320 UTF-16 units').to.equal(200);
  });
});

/** setTimeout by hand: `advance` fires what is due, in order. */
class ManualTimers implements FollowupTimers {
  private now = 0;
  private seq = 0;
  private readonly due = new Map<number, { at: number; callback: () => void }>();

  setTimeout(callback: () => void, ms: number): unknown {
    this.seq += 1;
    this.due.set(this.seq, { at: this.now + ms, callback });
    return this.seq;
  }

  clearTimeout(handle: unknown): void {
    this.due.delete(handle as number);
  }

  get pending(): number {
    return this.due.size;
  }

  async advance(ms: number): Promise<void> {
    const end = this.now + ms;
    for (;;) {
      const next = [...this.due.entries()].sort((a, b) => a[1].at - b[1].at)[0];
      if (next === undefined || next[1].at > end) break;
      this.due.delete(next[0]);
      this.now = next[1].at;
      next[1].callback();
      await turn();
    }
    this.now = end;
  }
}

const ORG = 'org-ux5';
const SESSION = 'ses-ux5';
const RUN_ID = 'run-ux5';
const identities: Record<string, Record<string, unknown>> = {
  owner: { orgId: ORG, userId: 'user-owner' },
  participant: { orgId: ORG, userId: 'user-participant' },
  viewer: { orgId: ORG, userId: 'user-viewer' },
  stranger: { orgId: ORG, userId: 'user-stranger' },
};

interface SessionWorld {
  run: HarnessRun;
  bus: InMemoryLiveBus<ActivityFrame>;
  timers: ManualTimers;
  reads: { count: number };
  get: (path: string, bearer: string) => Promise<Response>;
  close: () => Promise<void>;
}

async function sessionWorld(options: { bus?: boolean; status?: HarnessRun['status']; followups?: HarnessRun['followups'] } = {}): Promise<SessionWorld> {
  const run = {
    runId: RUN_ID,
    orgId: ORG,
    sessionId: SESSION,
    actorId: 'user-owner',
    agentKey: 'agent-1',
    harnessMajor: 2,
    status: options.status ?? 'completed',
    ...(options.followups !== undefined ? { followups: options.followups } : {}),
    createdAt: 1,
    updatedAt: 2,
  } as unknown as HarnessRun;
  const reads = { count: 0 };
  const sessions = {
    findStreamAccess: (scope: { actorId: string; sessionId: string }) =>
      Promise.resolve(
        scope.sessionId !== SESSION
          ? null
          : scope.actorId === 'user-owner' || scope.actorId === 'user-participant'
            ? { audience: 'owner' }
            : scope.actorId === 'user-viewer'
              ? { audience: 'shared' }
              : null,
      ),
  } as unknown as ChatSessionRepository;
  const executions = {
    get: (_org: string, runId: string) => {
      reads.count += 1;
      return Promise.resolve(runId === run.runId ? structuredClone(run) : null);
    },
  } as unknown as ExecutionService;
  const bus = new InMemoryLiveBus<ActivityFrame>(4_000, ACTIVITY_LIVE_CHANNEL);
  const timers = new ManualTimers();
  const runs = new SessionRunService(executions, sessions);
  const container = new Container();
  container.bind('AuthMiddleware').toConstantValue({
    authenticate: (req: express.Request & { user?: unknown }, res: express.Response, next: express.NextFunction) => {
      const identity = identities[(req.headers.authorization ?? '').replace(/^Bearer /, '')];
      if (identity === undefined) {
        res.status(401).json({});
        return;
      }
      req.user = identity;
      next();
    },
  });
  container.bind(AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue(runs);
  container.bind(AGENT_HARNESS_RUN_ADMISSION_SERVICE).toConstantValue({});
  container
    .bind(AGENT_HARNESS_RUN_FOLLOWUPS)
    .toConstantValue(new RunFollowupsService({ runs, executions, bus: options.bus === false ? null : bus, timers }));
  const quiet = [sinon.stub(Logger.getInstance(), 'error'), sinon.stub(Logger.getInstance(), 'warn'), sinon.stub(Logger.getInstance(), 'info')];
  const app = express();
  app.use('/agents', createAgentHarnessSessionRouter(container));
  app.use('/conversations', createChatHarnessSessionRouter(container));
  app.use(ErrorMiddleware.handleError());
  const { server, base } = await listen(app);
  return {
    run,
    bus,
    timers,
    reads,
    get: (path, bearer) => fetch(`${base}${path}`, { headers: { authorization: `Bearer ${bearer}` } }),
    close: async () => {
      for (const stub of quiet) stub.restore();
      await closeServer(server);
    },
  };
}

const FOLLOWUPS = `/agents/agent-1/conversations/${SESSION}/runs/${RUN_ID}/followups`;
const followupsFrame = (texts: string[]): ActivityFrame => ({
  runId: RUN_ID,
  fence: 1,
  modelAttempt: 0,
  n: 0,
  kind: 'followups',
  data: { items: texts.map((text) => ({ text })) },
});
const body = async (reply: Response) => ((await reply.json()) as { followups: unknown }).followups;

describe('agent-harness follow-ups: the owner\'s session route (UX5)', () => {
  let world: SessionWorld;
  afterEach(async () => {
    await world.close();
  });

  it('answers the stored follow-ups at once, on both routers', async () => {
    const items = [{ text: 'Compare with last quarter' }];
    world = await sessionWorld({ followups: items });
    const reply = await world.get(`${FOLLOWUPS}?waitMs=12000`, 'owner');
    expect(reply.status).to.equal(200);
    expect(reply.headers.get('cache-control')).to.equal('no-store');
    expect(await body(reply)).to.deep.equal(items);
    expect(world.timers.pending, 'nothing waited').to.equal(0);
    expect(await body(await world.get(`/conversations/${SESSION}/runs/${RUN_ID}/followups`, 'owner'))).to.deep.equal(items);
  });

  it('anyone but the run\'s actor is told the run is not found', async () => {
    world = await sessionWorld({ followups: [{ text: 'Private' }] });
    for (const who of ['participant', 'viewer', 'stranger']) {
      expect((await world.get(FOLLOWUPS, who)).status, who).to.equal(404);
    }
  });

  it('waitMs defaults to 0: nothing stored answers [] without waiting; above 15000 is refused', async () => {
    world = await sessionWorld();
    expect(await body(await world.get(FOLLOWUPS, 'owner'))).to.deep.equal([]);
    expect(world.timers.pending).to.equal(0);
    expect((await world.get(`${FOLLOWUPS}?waitMs=15001`, 'owner')).status).to.equal(400);
    expect((await world.get(`${FOLLOWUPS}?waitMs=-1`, 'owner')).status).to.equal(400);
  });

  it('a run that failed has none to wait for: [] at once', async () => {
    world = await sessionWorld({ status: 'failed' });
    expect(await body(await world.get(`${FOLLOWUPS}?waitMs=12000`, 'owner'))).to.deep.equal([]);
    expect(world.timers.pending).to.equal(0);
  });

  it('a followups frame wakes a waiting request, which answers what the store holds', async () => {
    world = await sessionWorld();
    const pending = world.get(`${FOLLOWUPS}?waitMs=12000`, 'owner');
    await until(() => world.timers.pending === 2);
    world.run.followups = [{ text: 'Stored one' }];
    world.bus.publish(followupsFrame(['Published one']));
    const reply = await pending;
    expect(await body(reply)).to.deep.equal([{ text: 'Stored one' }]);
    expect(world.timers.pending, 'its timers are cleared').to.equal(0);
  });

  it('a frame that arrives before the store write answers with the frame\'s items', async () => {
    world = await sessionWorld();
    const pending = world.get(`${FOLLOWUPS}?waitMs=12000`, 'owner');
    await until(() => world.timers.pending === 2);
    world.bus.publish(followupsFrame(['Draft an email', 'Compare with last quarter']));
    expect(await body(await pending)).to.deep.equal([{ text: 'Draft an email' }, { text: 'Compare with last quarter' }]);
  });

  it('a frame already on the bus answers at once', async () => {
    world = await sessionWorld();
    world.bus.publish(followupsFrame(['Already there']));
    expect(await body(await world.get(`${FOLLOWUPS}?waitMs=12000`, 'owner'))).to.deep.equal([{ text: 'Already there' }]);
  });

  it('without a bus the store is re-read once a second until they are there', async () => {
    world = await sessionWorld({ bus: false });
    const pending = world.get(`${FOLLOWUPS}?waitMs=12000`, 'owner');
    await until(() => world.timers.pending === 2);
    const before = world.reads.count;
    await world.timers.advance(1_000);
    expect(world.reads.count, 'one re-read after a second').to.equal(before + 1);
    world.run.followups = [{ text: 'Polled' }];
    await world.timers.advance(1_000);
    expect(await body(await pending)).to.deep.equal([{ text: 'Polled' }]);
  });

  it('when nothing comes, the wait ends at waitMs with []', async () => {
    world = await sessionWorld();
    const pending = world.get(`${FOLLOWUPS}?waitMs=3000`, 'owner');
    await until(() => world.timers.pending === 2);
    world.bus.publish({ ...followupsFrame([]), data: { items: [] } });
    await world.timers.advance(2_999);
    await world.timers.advance(1);
    expect(await body(await pending)).to.deep.equal([]);
    expect(world.timers.pending).to.equal(0);
  });
});
