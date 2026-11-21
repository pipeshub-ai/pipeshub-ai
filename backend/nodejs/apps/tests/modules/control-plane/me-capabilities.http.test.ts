/**
 * CPD10-30, CPD10-31: `GET /api/v1/control-plane/me/capabilities` over real
 * HTTP: the real router and auth middleware (a real session token), the
 * harness's `RoutingCapabilities` over the decider that routes turns in each
 * mode (today's rollout; the resolved run configuration on the production
 * pinner), and the routing table's own answers as the expectation.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import { readFileSync } from 'node:fs';
import { Server } from 'node:http';
import { AddressInfo } from 'node:net';
import { join } from 'node:path';
import { Container } from 'inversify';
import yaml from 'js-yaml';
import { Types } from 'mongoose';
import sinon from 'sinon';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { AuthTokenService } from '../../../src/libs/services/authtoken.service';
import { Logger } from '../../../src/libs/services/logger.service';
import { HarnessRollout } from '../../../src/modules/agent-harness/application/harness-rollout';
import { ResolvedRoutingDecider } from '../../../src/modules/agent-harness/application/resolved-routing';
import { RolloutRoutingDecider } from '../../../src/modules/agent-harness/application/rollout-routing';
import {
  ROUTED_AGENT_MODES,
  ROUTED_ASSISTANT_MODES,
  RoutingCapabilities,
} from '../../../src/modules/agent-harness/application/routing-capabilities';
import { RoutingFactsReader } from '../../../src/modules/agent-harness/application/routing-facts';
import type { HarnessRolloutSettings } from '../../../src/modules/agent-harness/domain/rollout';
import type { RoutingDecider, RoutingSubject } from '../../../src/modules/agent-harness/ports/routing-decider';
import { UserActivities } from '../../../src/modules/auth/schema/userActivities.schema';
import {
  CONTROL_PLANE_ME_CAPABILITIES,
  CONTROL_PLANE_ME_CAPABILITIES_PATH,
  createMeCapabilitiesRouter,
} from '../../../src/modules/control-plane';
import { AGENT_CHAT_MODES } from '../../../src/modules/enterprise_search/validators/es_validators';
import { Users } from '../../../src/modules/user_management/schema/users.schema';
import { AGENT_MODES, ASSISTANT_MODES } from '../agent-harness/support/routing-decider-contract';
import { legacyWorld } from '../agent-harness/support/resolved-turn-world';
import { levelsOf } from '../agent-harness/support/routing-vectors';
import { InMemoryHeadRevisions } from './support/head-revisions-contract';

const ORG = new Types.ObjectId().toHexString();
const OTHER_ORG = new Types.ObjectId().toHexString();
const USER = new Types.ObjectId().toHexString();
const tokens = new AuthTokenService('cpd10-jwt', 'cpd10-scoped');
const session = (): string => tokens.generateToken({ userId: USER, orgId: ORG, email: 'ann@example.com', role: 'member' });

type Mode = 'today' | 'resolved';

interface Rig {
  get(headers?: Record<string, string>, query?: string): Promise<Response>;
  heads: InMemoryHeadRevisions;
  asked: RoutingSubject[];
  container: Container;
  close(): Promise<void>;
}

/** The table's answer for a mode, as the route states it. */
const expected = (modes: ReadonlyArray<[string, string]>): Record<string, { path: string; reason: string | null }> =>
  Object.fromEntries(
    modes.map(([mode, answer]) => [mode, answer === 'durable' ? { path: 'durable', reason: null } : { path: 'legacy', reason: answer }]),
  );

async function start(mode: Mode, settings: Partial<HarnessRolloutSettings> = {}, bound = true): Promise<Rig> {
  const whole: HarnessRolloutSettings = { infrastructureReady: true, killSwitch: false, orgEnabled: true, modelFamilies: null, ...settings };
  const world = await legacyWorld({ settings: whole });
  const facts = { sessions: { placement: async () => null }, liveness: { live: async () => true } };
  const deciding: RoutingDecider =
    mode === 'today'
      ? new RolloutRoutingDecider({ rollout: new HarnessRollout({ read: async () => whole }, { resolve: async () => null }), ...facts })
      : new ResolvedRoutingDecider({ pinner: world.pinner, facts: new RoutingFactsReader({ infrastructureReady: () => true, ...facts }) });
  const asked: RoutingSubject[] = [];
  const decider: RoutingDecider = {
    decide: (subject) => {
      asked.push(subject);
      return deciding.decide(subject);
    },
  };
  const container = new Container();
  container.bind('AuthMiddleware').toConstantValue(new AuthMiddleware(Logger.getInstance(), tokens, () => null));
  if (bound) {
    container.bind(CONTROL_PLANE_ME_CAPABILITIES).toConstantValue(new RoutingCapabilities({ decider, heads: world.heads }));
  }
  sinon.stub(Users, 'findOne').callsFake((() => ({ lean: () => ({ exec: async () => ({ _id: USER, orgId: ORG }) }) })) as never);
  sinon.stub(UserActivities, 'findOne').callsFake((() => ({ sort: () => ({ lean: () => ({ exec: async () => null }) }) })) as never);
  for (const level of ['error', 'warn', 'info', 'debug'] as const) sinon.stub(Logger.getInstance(), level);
  const app = express();
  app.use(express.json());
  app.use(CONTROL_PLANE_ME_CAPABILITIES_PATH, createMeCapabilitiesRouter(container));
  app.use(ErrorMiddleware.handleError());
  const server = await new Promise<Server>((resolve) => {
    const listening = app.listen(0, '127.0.0.1', () => resolve(listening));
  });
  const base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}${CONTROL_PLANE_ME_CAPABILITIES_PATH}`;
  return {
    get: (headers = { authorization: `Bearer ${session()}` }, query = '') => fetch(`${base}${query}`, { headers }),
    heads: world.heads,
    asked,
    container,
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
  };
}

describe('CPD-10 §6.3: GET /api/v1/control-plane/me/capabilities', () => {
  let rig: Rig | undefined;

  afterEach(async () => {
    await rig?.close();
    rig = undefined;
    sinon.restore();
  });

  it('CPD10-30: 401 without a session, and nothing is decided', async () => {
    rig = await start('today');
    expect((await rig.get({})).status).to.equal(401);
    expect((await rig.get({ authorization: 'Bearer not-a-token' })).status).to.equal(401);
    expect(rig.asked).to.deep.equal([]);
  });

  for (const mode of ['today', 'resolved'] as const) {
    it(`CPD10-30: every chat mode's route from the ${mode === 'today' ? "rollout's decider (off, shadow)" : 'resolved decider (on)'}, as the routing table states it`, async () => {
      rig = await start(mode);
      rig.heads.revisions.set(ORG, 7);
      const reply = await rig.get();
      expect(reply.status).to.equal(200);
      expect(reply.headers.get('etag')).to.equal('"7"');
      expect(await reply.json()).to.deep.equal({
        routing: { agent: expected(AGENT_MODES), assistant: expected(ASSISTANT_MODES) },
        headRevision: 7,
      });
    });

    it(`CPD10-31 (${mode}): with the kill switch on every mode is legacy, kill_switch`, async () => {
      rig = await start(mode, { killSwitch: true });
      const { routing } = (await (await rig.get()).json()) as { routing: Record<string, Record<string, unknown>> };
      const routes = [...Object.values(routing['agent']!), ...Object.values(routing['assistant']!)];
      expect(routes).to.have.length(ROUTED_AGENT_MODES.length + ROUTED_ASSISTANT_MODES.length);
      for (const route of routes) expect(route).to.deep.equal({ path: 'legacy', reason: 'kill_switch' });
    });
  }

  it('CPD10-30: the org and the user are the token’s, whatever the request says; each mode is asked for a new conversation without a project or a model', async () => {
    rig = await start('today');
    const reply = await rig.get(
      { authorization: `Bearer ${session()}`, 'x-org-id': OTHER_ORG },
      `?orgId=${OTHER_ORG}&userId=someone-else`,
    );
    expect(reply.status).to.equal(200);
    expect(new Set(rig.asked.map((subject) => `${subject.orgId}/${subject.actorId}`))).to.deep.equal(new Set([`${ORG}/${USER}`]));
    for (const subject of rig.asked) {
      expect(subject).to.not.have.any.keys('sessionId', 'projectId', 'scheduled');
      expect(Object.keys(subject.model)).to.deep.equal(['chatMode']);
    }
    const agentTurns = rig.asked.filter((subject) => subject.agentKey !== undefined);
    expect(agentTurns.map((subject) => subject.model.chatMode)).to.deep.equal([...ROUTED_AGENT_MODES]);
    expect(rig.asked.filter((subject) => subject.agentKey === undefined).map((subject) => subject.model.chatMode)).to.deep.equal([
      ...ROUTED_ASSISTANT_MODES,
    ]);
  });

  it('CPD10-30: `ETag` is the org head; a matching `If-None-Match` is 304 without deciding anything; a moved head answers again', async () => {
    rig = await start('resolved');
    rig.heads.revisions.set(ORG, 3);
    const first = await rig.get();
    expect(first.headers.get('etag')).to.equal('"3"');
    await first.text();
    const decided = rig.asked.length;
    for (const tag of ['"3"', 'W/"3"', '"1", "3"', '*']) {
      const again = await rig.get({ authorization: `Bearer ${session()}`, 'if-none-match': tag });
      expect(again.status, tag).to.equal(304);
      expect(again.headers.get('etag')).to.equal('"3"');
      expect(await again.text()).to.equal('');
    }
    expect(rig.asked.length, 'a 304 costs one head read and no decision').to.equal(decided);
    rig.heads.revisions.set(ORG, 4);
    const moved = await rig.get({ authorization: `Bearer ${session()}`, 'if-none-match': '"3"' });
    expect(moved.status).to.equal(200);
    expect(moved.headers.get('etag')).to.equal('"4"');
    expect(((await moved.json()) as { headRevision: number }).headRevision).to.equal(4);
  });

  it('CPD10-30: a decider that cannot decide is 503, never a guessed route', async () => {
    rig = await start('resolved');
    const failing = new RoutingCapabilities({
      decider: { decide: async () => Promise.reject(new Error('snapshot unreadable')) },
      heads: rig.heads,
    });
    rig.container.rebind(CONTROL_PLANE_ME_CAPABILITIES).toConstantValue(failing);
    const reply = await rig.get();
    expect(reply.status).to.equal(503);
    expect(JSON.stringify(await reply.json())).to.not.include('snapshot unreadable');
  });

  it('CPD10-30: an org head that cannot be read is 503', async () => {
    rig = await start('resolved');
    rig.heads.unreadable = true;
    expect((await rig.get()).status).to.equal(503);
    expect(rig.asked).to.deep.equal([]);
  });

  it('CPD10-30: a deployment that routes no turns answers 503', async () => {
    rig = await start('today', {}, false);
    expect((await rig.get()).status).to.equal(503);
  });

  it('the modes answered are the routing table’s and the chat routes’ own', () => {
    expect([...ROUTED_AGENT_MODES]).to.deep.equal([...AGENT_CHAT_MODES]);
    expect(AGENT_MODES.map(([mode]) => mode)).to.deep.equal([...ROUTED_AGENT_MODES]);
    expect(ASSISTANT_MODES.map(([mode]) => mode)).to.deep.equal([...ROUTED_ASSISTANT_MODES]);
    const known = levelsOf('chatMode');
    for (const mode of [...ROUTED_AGENT_MODES, ...ROUTED_ASSISTANT_MODES]) expect(known, mode).to.include(mode);
  });

  it('CPD10-30: the route and its body are in the OpenAPI document', () => {
    const spec = yaml.load(
      readFileSync(join(__dirname, '../../../src/modules/api-docs/pipeshub-openapi.yaml'), 'utf8'),
    ) as { paths: Record<string, { get?: { responses: Record<string, unknown> } }>; components: { schemas: Record<string, { required?: string[] }> } };
    const operation = spec.paths['/control-plane/me/capabilities']?.get;
    expect(Object.keys(operation?.responses ?? {})).to.include.members(['200', '304', '401', '503']);
    expect(spec.components.schemas['MeCapabilities']?.required).to.deep.equal(['routing', 'headRevision']);
  });
});
