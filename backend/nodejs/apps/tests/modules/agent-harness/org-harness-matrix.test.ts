/**
 * ADR-OPS-08: with the server switch on and an org's Labs "Agent harness" off, every behaviour the org sees is the
 * one an install with the switch off gives; with Labs on it is the harness's. One row per item of the ADR's table
 * that Node owns; each row runs the item three ways (switch off, switch on + Labs off, switch on + Labs on).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import express, { type Request, type RequestHandler, type Response } from 'express';
import jwt from 'jsonwebtoken';
import type { AddressInfo } from 'node:net';
import { Container } from 'inversify';
import {
  erasureOnDelete,
  orgHarnessOn,
  recordOrgHarnessReader,
} from '../../../src/modules/agent-harness/config';
import {
  byOrgErasure,
  sessionForks,
  sessionParticipants,
} from '../../../src/modules/agent-harness/composition';
import {
  AGENT_HARNESS_SESSION_FORKS,
  AGENT_HARNESS_SESSION_PARTICIPANTS,
} from '../../../src/modules/agent-harness/binders/tokens';
import { LegacyTurnHeader } from '../../../src/modules/agent-harness/application/legacy-turn-header';
import { OrgScopedRoutingDecider } from '../../../src/modules/agent-harness/application/resolved-routing';
import type { RoutingDecider } from '../../../src/modules/agent-harness/ports/routing-decider';
import {
  harnessNamespaceWhileOff,
  whenHarnessSwitchedOn,
} from '../../../src/modules/agent-harness/transport/harness-switch';
import { internalUploadMaxBytes } from '../../../src/modules/storage/utils/internal-upload-limit';
import { expandChatCommand } from '../../../src/modules/enterprise_search/utils/chat-command';
import { runWithOrgContext, makeOrgContext } from '../../../src/libs/context/orgContext';
import { HarnessRollout } from '../../../src/modules/agent-harness/application/harness-rollout';
import type { HarnessRolloutSettings } from '../../../src/modules/agent-harness/domain/rollout';
import { getEffectivePlatformFeatureFlags } from '../../../src/modules/configuration_manager/controller/cm_controller';

const ORG = 'org-1';
const MIB = 1024 * 1024;

type World = 'switch off' | 'switch on, Labs off' | 'switch on, Labs on';
const WORLDS: readonly World[] = ['switch off', 'switch on, Labs off', 'switch on, Labs on'];

/** Sets the switch in `process.env` and the org's Labs answer for one world, for the body's duration. */
async function inWorld<T>(world: World, body: () => Promise<T>): Promise<T> {
  const saved = process.env.AGENT_HARNESS_WORKER_ENABLED;
  const savedHardened = process.env.PIPESHUB_HARDENED;
  process.env.AGENT_HARNESS_WORKER_ENABLED = world === 'switch off' ? 'false' : 'true';
  // The compatibility profile: the hardened one erases on every delete, harness or not (ADR-OPS-07).
  delete process.env.PIPESHUB_HARDENED;
  recordOrgHarnessReader(() => Promise.resolve(world === 'switch on, Labs on'));
  try {
    return await body();
  } finally {
    process.env.AGENT_HARNESS_WORKER_ENABLED = saved;
    if (savedHardened === undefined) delete process.env.PIPESHUB_HARDENED;
    else process.env.PIPESHUB_HARDENED = savedHardened;
  }
}

/** The item's observable answer in each world: off and Labs off must be equal, Labs on must be the harness's. */
async function matrix<T>(item: () => Promise<T>): Promise<Record<World, T>> {
  const out = {} as Record<World, T>;
  for (const world of WORLDS) out[world] = await inWorld(world, item);
  return out;
}

function expectMatrix<T>(answers: Record<World, T>, harness: T): void {
  expect(answers['switch on, Labs off']).to.deep.equal(answers['switch off']);
  expect(answers['switch on, Labs on']).to.deep.equal(harness);
  expect(answers['switch off']).to.not.deep.equal(harness);
}

const bearer = (orgId: string): string =>
  `Bearer ${jwt.sign({ orgId, userId: 'user-1' }, 'not-verified-here')}`;

async function serve(
  app: express.Express,
  path: string,
  orgId: string | null,
): Promise<{ status: number; body: string }> {
  const server = app.listen(0);
  try {
    const { port } = server.address() as AddressInfo;
    const res = await fetch(`http://127.0.0.1:${port}${path}`, {
      headers: orgId === null ? {} : { authorization: bearer(orgId) },
    });
    return { status: res.status, body: await res.text() };
  } finally {
    server.close();
  }
}

describe('ADR-OPS-08: switch on + Labs off is switch off, for every item Node owns', () => {
  afterEach(() => {
    recordOrgHarnessReader(() => Promise.resolve(true));
  });

  it('the predicate itself: the switch and the org\'s Labs; no org, no reader or a failing read is off', async () => {
    expectMatrix(await matrix(() => orgHarnessOn(ORG)), true);
    await inWorld('switch on, Labs on', async () => {
      expect(await orgHarnessOn(null)).to.equal(false);
      recordOrgHarnessReader(() => Promise.reject(new Error('store down')));
      expect(await orgHarnessOn(ORG)).to.equal(false);
      recordOrgHarnessReader(null);
      expect(await orgHarnessOn(ORG)).to.equal(false);
    });
  });

  it('erasure on delete (chats, people, service accounts, the org): a soft delete, as before', async () => {
    expectMatrix(await matrix(() => erasureOnDelete(ORG)), true);
  });

  it('a chat delete route: built without the erasure queue for the org', async () => {
    const queue = { enqueue: async () => true };
    const answers = await matrix(async () => {
      let used: unknown = 'unset';
      const handler = byOrgErasure(queue, (q) => (_req, res) => {
        used = q;
        res.end();
      });
      await new Promise<void>((resolve) => {
        void handler(
          { user: { orgId: ORG } } as unknown as Request,
          { end: resolve } as unknown as Response,
          () => resolve(),
        );
      });
      return used === queue;
    });
    expectMatrix(answers, true);
  });

  it('the internal upload route\'s cap: the platform\'s 30 MiB', async () => {
    const answers = await matrix(async () =>
      internalUploadMaxBytes(30 * MIB, await orgHarnessOn(ORG), process.env),
    );
    expectMatrix(answers, 100 * MIB);
  });

  it('server-side / commands: the message goes on as typed', async () => {
    const answers = await matrix(async () => {
      const calls: string[] = [];
      const handler = expandChatCommand(async (_req, name) => {
        calls.push(name);
        return { kind: 'not_found' } as never;
      });
      const req = {
        body: { query: '/triage INC-1' },
        headers: {},
        user: { orgId: ORG, userId: 'user-1' },
      } as unknown as Request;
      await new Promise<unknown>((resolve) => {
        void handler(req, { locals: {} } as unknown as Response, resolve);
      });
      return calls;
    });
    expectMatrix(answers, ['triage']);
  });

  it('the harness\'s route families: the request falls through to the classic routes', async () => {
    const answers = await matrix(async () => {
      const app = express();
      const harness = express.Router();
      harness.get('/:id/runs', (_req, res) => {
        res.send('harness');
      });
      app.use('/api/v1/conversations', whenHarnessSwitchedOn(harness));
      app.use('/api/v1/conversations', (_req, res) => {
        res.status(404).send('classic');
      });
      return (await serve(app, '/api/v1/conversations/c1/runs', ORG)).body;
    });
    expectMatrix(answers, 'harness');
  });

  it('the harness\'s own namespace: a JSON 404 before any harness route', async () => {
    const answers = await matrix(async () => {
      const app = express();
      app.use('/api/v1/agent-harness', harnessNamespaceWhileOff());
      app.get('/api/v1/agent-harness/admin', (_req, res) => {
        res.send('harness');
      });
      // eslint-disable-next-line @typescript-eslint/no-unused-vars -- Express knows an error handler by its four parameters
      app.use(((err: { statusCode?: number }, _req, res, _next) => {
        res.status(err.statusCode ?? 500).send('refused');
      }) as express.ErrorRequestHandler);
      const { status, body } = await serve(app, '/api/v1/agent-harness/admin', ORG);
      return `${status} ${body}`;
    });
    expectMatrix(answers, '200 harness');
  });

  it('the classic turn\'s pin: no X-PipesHub-Resolved header, so no RUN_CONFIG_UNAVAILABLE path', async () => {
    const answers = await matrix(async () => {
      const header = new LegacyTurnHeader({
        resolver: { resolve: async () => ({}) as never },
        authority: () => null,
        harnessOn: (orgId) => orgHarnessOn(orgId),
      });
      try {
        return await header.prepare({ orgId: ORG, actorId: 'user-1', agentKey: null, chatMode: 'quick' });
      } catch (error) {
        return (error as { code?: string; name?: string }).name ?? 'refused';
      }
    });
    // Labs on: the harness pins the turn, and with no signing keys refuses it.
    expect(answers['switch off']).to.equal(null);
    expect(answers['switch on, Labs off']).to.equal(null);
    expect(answers['switch on, Labs on']).to.not.equal(null);
  });

  it('who routes a chat turn: today\'s decider, not the resolved run configuration', async () => {
    const decider = (name: string): RoutingDecider => ({
      decide: async () => ({ routing: { useHarness: false, harnessMajor: 2, reason: 'org_disabled' }, scope: { agentKey: name } }),
    });
    const answers = await matrix(async () => {
      const routed = new OrgScopedRoutingDecider((orgId) => orgHarnessOn(orgId), decider('resolved'), decider('today'));
      return (await routed.decide({ orgId: ORG, actorId: 'user-1', model: { chatMode: 'quick' } as never })).scope.agentKey;
    });
    expectMatrix(answers, 'resolved');
  });

  it('sharing and Continue privately: no participant recorder and no fork service for the org', async () => {
    const container = new Container();
    container.bind(AGENT_HARNESS_SESSION_PARTICIPANTS).toConstantValue({ recorder: true });
    container.bind(AGENT_HARNESS_SESSION_FORKS).toConstantValue({ forks: true });
    const answers = await matrix(() =>
      runWithOrgContext(
        makeOrgContext({ orgId: ORG, userId: 'user-1', kind: 'user', source: 'jwt' }),
        async () => [
          (await sessionParticipants(container)()) !== undefined,
          (await sessionForks(container)()) !== undefined,
        ],
      ),
    );
    expectMatrix(answers, [true, true]);
  });

  for (const [stop, settings] of [
    ['kill switch', { infrastructureReady: true, killSwitch: true }],
    ['infrastructure not ready', { infrastructureReady: false, killSwitch: false }],
  ] as const) {
    it(`${stop} + Labs on: the org's surfaces stay (harnessOn), a new run is refused (useHarness)`, async () => {
      const rollout = new HarnessRollout(
        {
          read: async (): Promise<HarnessRolloutSettings> => ({ ...settings, orgEnabled: true, modelFamilies: null }),
        },
        { resolve: async () => null } as never,
      );
      let body: { agentHarness: { harnessOn: boolean; useHarness: boolean; reason: string } } | undefined;
      const res = {
        status: () => res,
        json: (sent: typeof body) => {
          body = sent;
          return res;
        },
        end: () => res,
      };
      const handler = getEffectivePlatformFeatureFlags(
        { get: async () => null } as never,
        rollout,
        { AGENT_HARNESS_WORKER_ENABLED: 'true' },
        async () => false,
      );
      await handler({ user: { orgId: ORG, userId: 'user-1' } } as never, res as never, (() => undefined) as never);
      expect(body?.agentHarness.harnessOn).to.equal(true);
      expect(body?.agentHarness.useHarness).to.equal(false);
      expect(body?.agentHarness.reason).to.equal(stop === 'kill switch' ? 'kill_switch' : 'infrastructure_unavailable');
    });
  }

  it('a request that names no org reaches the family, whose own authentication answers it', async () => {
    await inWorld('switch on, Labs off', async () => {
      const app = express();
      const harness = express.Router();
      const guard: RequestHandler = (_req, res) => {
        res.status(401).send('unauthenticated');
      };
      harness.use(guard);
      app.use('/api/v1/agents', whenHarnessSwitchedOn(harness));
      expect((await serve(app, '/api/v1/agents/a/export', null)).body).to.equal('unauthenticated');
    });
  });
});
