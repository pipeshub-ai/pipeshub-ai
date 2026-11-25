/**
 * CPD10-20, CPD10-21, CPD10-28, CPD10-29: `X-PipesHub-Resolved` on the legacy
 * chat proxies, over real HTTP. The app is `app.ts`'s own middleware chain
 * with the real chat routers, validators, auth middleware (real session,
 * OAuth and scoped tokens) and legacy handlers; Mongo is the in-memory chat
 * store and the query service a recording HTTP stub. Each header the stub
 * receives is held to what the query service checks: it verifies with the
 * published key for the identity of the bearer the same request forwards,
 * the agent of the stub's own route and the conversation of its body.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import jwt from 'jsonwebtoken';
import mongoose, { Types } from 'mongoose';
import { createPublicKey } from 'node:crypto';
import http, { IncomingHttpHeaders, Server } from 'node:http';
import { AddressInfo } from 'node:net';
import { Container } from 'inversify';
import sinon from 'sinon';
import { Application } from '../../../src/app';
import { AI_RESOLVED_TURN_HEADER } from '../../../src/libs/commands/ai_service/ai.service.command';
import { TokenScopes } from '../../../src/libs/enums/token-scopes.enum';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { AuthTokenService } from '../../../src/libs/services/authtoken.service';
import { Logger } from '../../../src/libs/services/logger.service';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { decodeAttemptToken, verifyAttemptSignature } from '../../../src/modules/agent-harness/application/attempt-token-codec';
import { HarnessRollout } from '../../../src/modules/agent-harness/application/harness-rollout';
import { LegacyTurnHeader } from '../../../src/modules/agent-harness/application/legacy-turn-header';
import { ResolvedRoutingDecider } from '../../../src/modules/agent-harness/application/resolved-routing';
import { RolloutRoutingDecider } from '../../../src/modules/agent-harness/application/rollout-routing';
import { RoutingFactsReader } from '../../../src/modules/agent-harness/application/routing-facts';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { AGENT_HARNESS_RESOLVED_ROUTING, routingDecider } from '../../../src/modules/agent-harness/binders/control-plane-pin';
import {
  AGENT_HARNESS_LEGACY_TURN_HEADER,
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_RUN_STREAMS,
  AGENT_HARNESS_SESSION_RUN_SERVICE,
} from '../../../src/modules/agent-harness/binders/tokens';
import { RESOLVED_TURN_HEADER } from '../../../src/modules/agent-harness/domain/resolved-turn';
import type { HarnessRolloutSettings } from '../../../src/modules/agent-harness/domain/rollout';
import type { ControlPlanePin, ResolvedTurn } from '../../../src/modules/control-plane';
import { UserActivities } from '../../../src/modules/auth/schema/userActivities.schema';
import * as cmController from '../../../src/modules/configuration_manager/controller/cm_controller';
import {
  createAgentConversationalRouter,
  createConversationalRouter,
} from '../../../src/modules/enterprise_search/routes/es.routes';
import { OAuthApp } from '../../../src/modules/oauth_provider/schema/oauth.app.schema';
import type { AppConfig } from '../../../src/modules/tokens_manager/config/config';
import { Org } from '../../../src/modules/user_management/schema/org.schema';
import { Users } from '../../../src/modules/user_management/schema/users.schema';
import { setSlackUserLinkRepository } from '../../../src/libs/repositories/slack-user-link';
import { SlackIdentityLinks } from '../../../src/modules/configuration_manager/slack-identity/slack-identity-links';
import { setSlackIdentityLinksForTests } from '../../../src/modules/configuration_manager/slack-identity/slack-linked-account';
import { InMemorySlackUserLinks } from '../../support/slack/in-memory-slack-links';
import { fakeReplicaSetSession, InMemoryChatStore } from '../enterprise_search/controller/chat-test-harness';
import { grantingAccess } from './support/agent-definition';
import { legacyWorld, RESOLVED_TURN_VECTOR, type LegacyWorld, type LegacyWorldOptions } from './support/resolved-turn-world';
import { registerUpstreams } from '../../../src/libs/http/upstream-call';
import { registerAppUpstreams } from '../../../src/modules/tokens_manager/config/config';

const JWT_SECRET = 'cpd10-jwt-secret';
const SCOPED_SECRET = 'cpd10-scoped-secret';
const ORG = new Types.ObjectId().toHexString();
const USER = new Types.ObjectId().toHexString();
const SERVICE_IDENTITY = new Types.ObjectId().toHexString();
const OAUTH_CLIENT = 'client-app-1';
const AGENT = 'agent-1';
const SLACK_EMAIL = 'sam@slack.example';
const SERVICE_EMAIL = 'bot@slack.example';
const PLACEHOLDER = 'agentIdPlaceholder';

interface Proxied {
  method: string;
  path: string;
  headers: IncomingHttpHeaders;
  body: Record<string, unknown>;
}

interface Rig {
  url: string;
  proxied: Proxied[];
  chat: () => Proxied[];
  store: InMemoryChatStore;
  world: LegacyWorld;
  send(path: string, token: string, body: Record<string, unknown>, headers?: Record<string, string>): Promise<Response>;
  close(): Promise<void>;
}

interface RigOptions extends LegacyWorldOptions {
  /** False: this deployment holds no gateway keyring. */
  signingKeys?: boolean;
}

const tokens = new AuthTokenService(JWT_SECRET, SCOPED_SECRET);

const sessionToken = (): string => tokens.generateToken({ userId: USER, orgId: ORG, email: 'ann@example.com', role: 'member' });
/** A client-credentials access token: its own `userId` is the client, and it acts as `createdBy`. */
const clientCredentialsToken = (): string =>
  jwt.sign(
    {
      userId: OAUTH_CLIENT,
      orgId: ORG,
      client_id: OAUTH_CLIENT,
      createdBy: SERVICE_IDENTITY,
      tokenType: 'oauth',
      iss: 'https://pipeshub.test',
      scope: 'conversation:chat agent:execute',
    },
    JWT_SECRET,
    { expiresIn: 600 },
  );
const SLACK_TEAM = 'T0CPD10';
/** The Slack bot's service credential for one workspace member: it names the bot's configuration and the person. */
const slackToken = (email: string, slackUserId: string): string =>
  tokens.generateScopedToken({
    email,
    configId: 'cfg-1',
    slackTeamId: SLACK_TEAM,
    slackUserId,
    scopes: [TokenScopes.CONVERSATION_CREATE],
  });

/** Who the query service takes the proxied request to be from: `authMiddleware` and `_get_user_context`. */
function queryServiceIdentity(authorization: string | undefined): { orgId: string; userId: string } {
  const token = String(authorization).replace(/^Bearer /, '');
  let payload: Record<string, unknown>;
  try {
    payload = jwt.verify(token, JWT_SECRET) as Record<string, unknown>;
    if (payload['tokenType'] === 'oauth' && payload['userId'] === payload['client_id'] && payload['createdBy']) {
      payload['userId'] = payload['createdBy'];
    }
  } catch {
    payload = jwt.verify(token, SCOPED_SECRET) as Record<string, unknown>;
  }
  return { orgId: String(payload['orgId']), userId: String(payload['userId']) };
}

/** The agent the query service reads from its own route (`resolved_turn_for`). */
function queryServiceAgent(path: string): string | null {
  const agent = /^\/api\/v1\/agent\/([^/]+)\/chat/.exec(path)?.[1];
  return agent === undefined || agent === PLACEHOLDER ? null : decodeURIComponent(agent);
}

const query = <T>(value: T) => {
  const chain = {
    select: () => chain,
    lean: () => chain,
    sort: () => chain,
    limit: () => chain,
    exec: () => Promise.resolve(value),
    then: <R>(resolve: (found: T) => R) => Promise.resolve(value).then(resolve),
  };
  return chain;
};

async function startQueryService(proxied: Proxied[]): Promise<Server> {
  const server = http.createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on('data', (chunk: Buffer) => chunks.push(chunk));
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      const path = String(req.url);
      proxied.push({
        method: String(req.method),
        path,
        headers: req.headers,
        body: raw === '' ? {} : (JSON.parse(raw) as Record<string, unknown>),
      });
      if (path.endsWith('/internal/service-account')) {
        res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify({ isServiceAccount: true }));
      } else if (path.endsWith('/stream')) {
        res.writeHead(200, { 'content-type': 'text/event-stream' });
        res.end(`event: complete\ndata: ${JSON.stringify({ answer: 'ok', citations: [], confidence: 'High' })}\n\n`);
      } else {
        res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify({ answer: 'ok', citations: [], confidence: 'High' }));
      }
    });
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  return server;
}

const address = (server: Server): string => `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;

/** One Node process in `mode`, as `bindControlPlanePin` and the admission binder wire it, in front of the stub. */
async function startRig(mode: ControlPlanePin, options: RigOptions = {}): Promise<Rig> {
  sinon.stub(process, 'env').value({
    ...process.env,
    CONTROL_PLANE_PIN: mode,
    REPLICA_SET_AVAILABLE: 'true',
    AGENT_HARNESS_RELAY_ENABLED: 'true',
    AGENT_HARNESS_WORKER_ENABLED: 'true',
  });
  const store = new InMemoryChatStore();
  store.install();
  sinon.stub(mongoose, 'startSession').callsFake((async () => fakeReplicaSetSession()) as never);
  const people: Record<string, unknown>[] = [
    { _id: new Types.ObjectId(USER), orgId: new Types.ObjectId(ORG), email: 'ann@example.com', fullName: 'Ann', role: 'member', slug: 'ann' },
    { _id: new Types.ObjectId(SERVICE_IDENTITY), orgId: new Types.ObjectId(ORG), email: 'svc@example.com', role: 'member', kind: 'service' },
    { _id: new Types.ObjectId(USER), orgId: new Types.ObjectId(ORG), email: SLACK_EMAIL, fullName: 'Sam', slug: 'sam' },
  ];
  sinon.stub(Users, 'findOne').callsFake(((filter: Record<string, unknown>) =>
    query(
      people.find((person) =>
        filter['email'] !== undefined ? person['email'] === filter['email'] : String(person['_id']) === String(filter['_id']),
      ) ?? null,
    )) as never);
  sinon.stub(UserActivities, 'findOne').callsFake((() => query(null)) as never);
  sinon.stub(OAuthApp, 'findOne').callsFake((() => query({ createdBy: new Types.ObjectId(SERVICE_IDENTITY) })) as never);
  sinon.stub(Org, 'find').callsFake((() => query([{ _id: new Types.ObjectId(ORG) }])) as never);
  sinon.stub(Org, 'findOne').callsFake((() => query({ _id: new Types.ObjectId(ORG), accountType: 'business' })) as never);
  sinon.stub(cmController, 'getSlackBotStore').resolves({ configs: [{ agentId: AGENT }] } as never);
  // Sam linked their Slack account to their PipesHub account; the bot's other member linked none.
  const slackLinks = new InMemorySlackUserLinks();
  await slackLinks.upsert({ orgId: ORG, workspaceId: SLACK_TEAM, slackUserId: 'U0SAM', teamId: SLACK_TEAM, userId: USER, email: SLACK_EMAIL, slackName: 'Sam' });
  setSlackUserLinkRepository(slackLinks);
  setSlackIdentityLinksForTests(new SlackIdentityLinks({ get: async () => null } as never, slackLinks));
  const quiet = Logger.getInstance();
  for (const level of ['error', 'warn', 'info', 'debug'] as const) sinon.stub(quiet, level);

  const proxied: Proxied[] = [];
  const queryService = await startQueryService(proxied);
  const settings: HarnessRolloutSettings = {
    infrastructureReady: true,
    killSwitch: false,
    orgEnabled: true,
    modelFamilies: null,
    ...options.settings,
  };
  const world = await legacyWorld({ ...options, settings });
  const container = new Container();
  const appConfig = { aiBackend: address(queryService), jwtSecret: JWT_SECRET, scopedJwtSecret: SCOPED_SECRET } as unknown as AppConfig;
  container.bind('AppConfig').toConstantValue(appConfig);
  registerAppUpstreams(appConfig);
  container.bind('KeyValueStoreService').toConstantValue({ get: async () => null });
  container.bind('AuthMiddleware').toConstantValue(
    new AuthMiddleware(quiet, tokens, () => ({ verifyAccessToken: async (token: string) => jwt.verify(token, JWT_SECRET) }) as never),
  );
  if (mode !== 'off') {
    container.bind(AGENT_HARNESS_RESOLVED_ROUTING).toConstantValue(
      new ResolvedRoutingDecider({
        pinner: world.pinner,
        facts: new RoutingFactsReader({
          infrastructureReady: () => true,
          sessions: { placement: async () => LEGACY_PLACEMENT },
          liveness: { live: async () => true },
        }),
      }),
    );
  }
  if (mode === 'on') {
    container
      .bind(AGENT_HARNESS_LEGACY_TURN_HEADER)
      .toConstantValue(
        options.signingKeys === false ? new LegacyTurnHeader({ resolver: world.resolver, authority: () => null }) : world.header,
      );
  }
  const today = new RolloutRoutingDecider({
    rollout: new HarnessRollout({ read: async () => settings }, { resolve: async () => null }),
    sessions: { placement: async () => LEGACY_PLACEMENT },
    liveness: { live: async () => true },
  });
  container.bind(AGENT_HARNESS_RUN_ADMISSION_SERVICE).toConstantValue(
    new RunAdmissionService({
      store: new InMemoryExecutionStore(),
      rollout: new HarnessRollout({ read: async () => settings }, { resolve: async () => null }),
      agentAccess: grantingAccess(),
      routing: routingDecider(container, today),
    }),
  );
  // Every turn of this suite is one today's loop serves: a durable one would fail here, loudly.
  container.bind(AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue({});
  container.bind(AGENT_HARNESS_RUN_STREAMS).toConstantValue({});

  const application = new Application() as unknown as {
    app: express.Express;
    logger: Logger;
    esAgentContainer: Container;
    configureMiddleware(config: AppConfig): void;
  };
  application.logger = quiet;
  application.esAgentContainer = container;
  application.configureMiddleware({
    ...appConfig,
    trustProxy: { value: false },
    maxRequestsPerMinute: 10_000,
  } as unknown as AppConfig);
  application.app.use('/api/v1/conversations', createConversationalRouter(container));
  application.app.use('/api/v1/agents', createAgentConversationalRouter(container));
  application.app.use(ErrorMiddleware.handleError());
  const node = await new Promise<Server>((resolve) => {
    const listening = application.app.listen(0, '127.0.0.1', () => resolve(listening));
  });
  return {
    url: address(node),
    proxied,
    chat: () => proxied.filter((call) => /\/chat(\/stream)?$/.test(call.path)),
    store,
    world,
    send: (path, token, body, headers = {}) =>
      fetch(`${address(node)}${path}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', authorization: `Bearer ${token}`, ...headers },
        body: JSON.stringify(body),
      }),
    close: async () => {
      await new Promise<void>((resolve) => node.close(() => resolve()));
      await new Promise<void>((resolve) => queryService.close(() => resolve()));
    },
  };
}

/** The header as the query service would take it: the published key, then its own request's identity, route and body. */
async function verifiedAsTheQueryServiceWould(rig: Rig, call: Proxied): Promise<ResolvedTurn> {
  const token = call.headers[RESOLVED_TURN_HEADER];
  expect(token, `${call.path}: a header`).to.be.a('string');
  const decoded = decodeAttemptToken(token as string, 16 * 1024);
  const published = (await rig.world.ring.published.read()).value!;
  const key = published.keys.find((listed) => listed.kid === (decoded.header as { kid: string }).kid);
  expect(key, 'signed by a published key').to.not.equal(undefined);
  expect(verifyAttemptSignature(decoded, createPublicKey(key!.publicKey))).to.equal(true);
  const identity = queryServiceIdentity(call.headers.authorization);
  return rig.world.authority.verify(token as string, {
    orgId: identity.orgId,
    userId: identity.userId,
    agentKey: queryServiceAgent(call.path),
    conversationId: (call.body['conversationId'] as string | null | undefined) ?? null,
  });
}

const seed = (store: InMemoryChatStore, agent: boolean, userId = USER) => {
  const session = store.addSession({
    orgId: ORG,
    userId,
    initiator: userId,
    title: 'Earlier chat',
    sessionType: agent ? 'agent' : 'chat',
    ...(agent ? { agentKey: AGENT, conversationSource: 'agent_chat' } : {}),
  });
  store.addMessage(session, { messageType: 'user_query', content: 'What changed in the release?' });
  const bot = store.addMessage(session, { messageType: 'bot_response', content: 'An older answer.' });
  return { conversationId: String(session._id), messageId: String(bot._id) };
};

/** Every seeded conversation has only today's turns: its record is read, and it has no durable turn. */
const LEGACY_PLACEMENT = { inProject: false, durable: false };

const CLIENT_HEADER = { [RESOLVED_TURN_HEADER]: 'client.supplied.bytes' };
/** A well-formed header, signed with a key this deployment never published. */
const FOREIGN_HEADER = { [RESOLVED_TURN_HEADER]: RESOLVED_TURN_VECTOR.refused.find((entry) => entry.id === 'unknown-kid')!.token };
const AGENT_TURN = { query: 'Plan the launch', chatMode: 'planExecute' };

describe('CPD-10: X-PipesHub-Resolved on the legacy chat proxies (the real app chain, a recording query service)', () => {
  let rig: Rig | undefined;

  afterEach(async () => {
    await rig?.close();
    rig = undefined;
    sinon.restore();
    registerUpstreams({});
    setSlackIdentityLinksForTests(undefined);
    setSlackUserLinkRepository();
  });

  describe('CPD10-20, CPD10-28: a client can never supply the header', () => {
    for (const mode of ['off', 'shadow'] as const) {
      it(`${mode}: the query service receives none, whatever the client sent, signed or not`, async () => {
        rig = await startRig(mode);
        const sent: Array<Record<string, string>> = [CLIENT_HEADER, FOREIGN_HEADER, { 'X-PipesHub-Resolved': 'mixed.case.name' }, {}];
        for (const supplied of sent) {
          const reply = await rig.send(`/api/v1/agents/${AGENT}/conversations/stream`, sessionToken(), AGENT_TURN, supplied);
          expect(reply.status).to.equal(200);
          await reply.text();
          const created = await rig.send('/api/v1/conversations/create', sessionToken(), { query: 'q', chatMode: 'agent' }, supplied);
          expect(created.status).to.equal(201);
        }
        expect(rig.chat()).to.have.length(8);
        for (const call of rig.chat()) {
          expect(call.headers).to.not.have.property(RESOLVED_TURN_HEADER);
          expect(call.headers.authorization, 'the rest of the request is forwarded as before').to.match(/^Bearer /);
        }
      });
    }

    for (const mode of ['off', 'shadow', 'on'] as const) {
      it(`${mode}: a route that proxies something other than a chat turn never forwards it either`, async () => {
        rig = await startRig(mode);
        const reply = await fetch(`${rig.url}/api/v1/agents/${AGENT}`, {
          headers: { authorization: `Bearer ${sessionToken()}`, ...FOREIGN_HEADER },
        });
        expect(reply.status).to.equal(200);
        const [call] = rig.proxied;
        expect(call).to.include({ method: 'GET', path: `/api/v1/agent/${AGENT}` });
        expect(call!.headers).to.not.have.property(RESOLVED_TURN_HEADER);
        expect(call!.headers.authorization).to.match(/^Bearer /);
      });
    }

    it('the query-service command forwards the header under the name Node signs it for', () => {
      expect(AI_RESOLVED_TURN_HEADER).to.equal(RESOLVED_TURN_HEADER);
    });

    it('off: nothing of the control plane is read for a legacy turn', async () => {
      rig = await startRig('off');
      await (await rig.send(`/api/v1/agents/${AGENT}/conversations/stream`, sessionToken(), AGENT_TURN, CLIENT_HEADER)).text();
      expect(rig.world.heads.reads).to.equal(0);
      expect(rig.world.readabilityReads()).to.equal(0);
    });

    it("on: the query service receives Node's, never the client's bytes, and a client header signed with another key is never forwarded", async () => {
      rig = await startRig('on');
      for (const supplied of [CLIENT_HEADER, FOREIGN_HEADER]) {
        const reply = await rig.send(`/api/v1/agents/${AGENT}/conversations/stream`, sessionToken(), AGENT_TURN, supplied);
        expect(reply.status).to.equal(200);
        await reply.text();
      }
      expect(rig.chat()).to.have.length(2);
      for (const call of rig.chat()) {
        const sent = call.headers[RESOLVED_TURN_HEADER];
        expect(sent).to.not.equal(CLIENT_HEADER[RESOLVED_TURN_HEADER]).and.not.equal(FOREIGN_HEADER[RESOLVED_TURN_HEADER]);
        const turn = await verifiedAsTheQueryServiceWould(rig, call);
        expect(turn).to.include({ org: ORG, sub: USER, agent: AGENT });
      }
    });
  });

  describe('CPD10-21: the header per mode', () => {
    it('on, a legacy decision: the claims name the session’s org and user, the route’s agent and the conversation the handler created', async () => {
      rig = await startRig('on', { approvals: { default: { flow: { level: 'ask' } } }, stages: { ENABLE_MCP: false } });
      const reply = await rig.send(`/api/v1/agents/${AGENT}/conversations/stream`, sessionToken(), AGENT_TURN);
      await reply.text();
      const [call] = rig.chat();
      const turn = await verifiedAsTheQueryServiceWould(rig, call!);
      const [created] = rig.store.sessions;
      expect(turn).to.include({ org: ORG, sub: USER, agent: AGENT, cid: String(created!._id) });
      expect(turn.pin.routing).to.deep.equal({ path: 'legacy', reason: 'chat_mode_not_supported' });
      expect(turn.pin.scope).to.deep.equal({ agentKey: AGENT, projectId: null });
      expect(turn.stages).to.include({ ENABLE_AGENT_HARNESS: true, AGENT_HARNESS_KILL_SWITCH: false, ENABLE_MCP: false });
      expect(turn.flow).to.deep.equal([{ level: 'ask', above: 'public' }]);
      expect(turn.exp - turn.iat).to.equal(60);
      expect(rig.world.heads.reads, 'the route and the pin share one head read once the snapshot is held').to.be.at.most(2);
    });

    it('on: a continued conversation’s header names that conversation, and a second turn is signed afresh for its own', async () => {
      rig = await startRig('on');
      const { conversationId } = seed(rig.store, true);
      await (await rig.send(`/api/v1/agents/${AGENT}/conversations/${conversationId}/messages/stream`, sessionToken(), AGENT_TURN)).text();
      await (await rig.send(`/api/v1/agents/${AGENT}/conversations/stream`, sessionToken(), AGENT_TURN)).text();
      const [continued, created] = rig.chat();
      expect((await verifiedAsTheQueryServiceWould(rig, continued!)).cid).to.equal(conversationId);
      expect((await verifiedAsTheQueryServiceWould(rig, created!)).cid).to.not.equal(conversationId);
    });

    it('on: the kill switch routes a search turn to today’s loop with its header', async () => {
      rig = await startRig('on', { settings: { killSwitch: true } });
      await (await rig.send('/api/v1/conversations/stream', sessionToken(), { query: 'q', chatMode: 'internal_search' })).text();
      const [call] = rig.chat();
      expect(call!.path).to.equal('/api/v1/chat/stream');
      const turn = await verifiedAsTheQueryServiceWould(rig, call!);
      expect(turn.pin.routing).to.deep.equal({ path: 'legacy', reason: 'kill_switch' });
      expect(turn.stages).to.include({ AGENT_HARNESS_KILL_SWITCH: true });
    });

    it('on: the agent directory’s denial sends no header, and the request goes on for the query service to refuse', async () => {
      rig = await startRig('on', { directory: { authorizeRun: async () => null } });
      const reply = await rig.send(`/api/v1/agents/${AGENT}/conversations/stream`, sessionToken(), AGENT_TURN, CLIENT_HEADER);
      await reply.text();
      const [call] = rig.chat();
      expect(call!.headers).to.not.have.property(RESOLVED_TURN_HEADER);
    });

    it('on: the directory’s "may run" travels as `access`', async () => {
      rig = await startRig('on', { directory: { authorizeRun: async (_org, agentKey) => ({ agentKey, definitionVersion: 1 }) } });
      await (await rig.send(`/api/v1/agents/${AGENT}/conversations/stream`, sessionToken(), AGENT_TURN)).text();
      expect((await verifiedAsTheQueryServiceWould(rig, rig.chat()[0]!)).access).to.deep.equal({ run: true });
    });
  });

  describe('CPD10-29: under `on` every proxy of a chat turn sends a header that verifies', () => {
    interface Site {
      name: string;
      request: (rig: Rig) => { path: string; token: string; body: Record<string, unknown> };
      proxiedTo: string;
      sub: string;
      status?: number;
    }
    const sites: Site[] = [
      {
        name: 'streaming, the assistant (Universal Agent Mode)',
        request: () => ({ path: '/api/v1/conversations/stream', token: sessionToken(), body: { query: 'q', chatMode: 'agent' } }),
        proxiedTo: `/api/v1/agent/${PLACEHOLDER}/chat/stream`,
        sub: USER,
      },
      {
        name: 'streaming, a follow-up to the assistant',
        request: (rig) => ({
          path: `/api/v1/conversations/${seed(rig.store, false).conversationId}/messages/stream`,
          token: sessionToken(),
          body: { query: 'and then?', chatMode: 'agent' },
        }),
        proxiedTo: `/api/v1/agent/${PLACEHOLDER}/chat/stream`,
        sub: USER,
      },
      {
        name: 'streaming, an agent',
        request: () => ({ path: `/api/v1/agents/${AGENT}/conversations/stream`, token: sessionToken(), body: AGENT_TURN }),
        proxiedTo: `/api/v1/agent/${AGENT}/chat/stream`,
        sub: USER,
      },
      {
        name: 'streaming, a follow-up to an agent',
        request: (rig) => ({
          path: `/api/v1/agents/${AGENT}/conversations/${seed(rig.store, true).conversationId}/messages/stream`,
          token: sessionToken(),
          body: AGENT_TURN,
        }),
        proxiedTo: `/api/v1/agent/${AGENT}/chat/stream`,
        sub: USER,
      },
      {
        name: 'regenerate, the assistant (the chat switch asks for the classic path, CTL-7)',
        request: (rig) => {
          const { conversationId, messageId } = seed(rig.store, false);
          return {
            path: `/api/v1/conversations/${conversationId}/message/${messageId}/regenerate`,
            token: sessionToken(),
            body: { chatMode: 'internal_search', harness: 'legacy' },
          };
        },
        proxiedTo: '/api/v1/chat/stream',
        sub: USER,
      },
      {
        name: 'regenerate, an agent',
        request: (rig) => {
          const { conversationId, messageId } = seed(rig.store, true);
          return {
            path: `/api/v1/agents/${AGENT}/conversations/${conversationId}/message/${messageId}/regenerate`,
            token: sessionToken(),
            body: { chatMode: 'planExecute' },
          };
        },
        proxiedTo: `/api/v1/agent/${AGENT}/chat/stream`,
        sub: USER,
      },
      {
        name: 'non-streaming, the assistant creates a conversation',
        request: () => ({ path: '/api/v1/conversations/create', token: sessionToken(), body: { query: 'q', chatMode: 'internal_search' } }),
        proxiedTo: '/api/v1/chat',
        sub: USER,
        status: 201,
      },
      {
        name: 'non-streaming, a follow-up to the assistant',
        request: (rig) => ({
          path: `/api/v1/conversations/${seed(rig.store, false).conversationId}/messages`,
          token: sessionToken(),
          body: { query: 'and then?', chatMode: 'internal_search' },
        }),
        proxiedTo: '/api/v1/chat',
        sub: USER,
      },
      {
        name: 'non-streaming, an agent creates a conversation',
        request: () => ({ path: `/api/v1/agents/${AGENT}/conversations`, token: sessionToken(), body: { query: 'q', chatMode: 'auto' } }),
        proxiedTo: `/api/v1/agent/${AGENT}/chat`,
        sub: USER,
        status: 201,
      },
      {
        name: 'non-streaming, a follow-up to an agent',
        request: (rig) => ({
          path: `/api/v1/agents/${AGENT}/conversations/${seed(rig.store, true).conversationId}/messages`,
          token: sessionToken(),
          body: { query: 'and then?', chatMode: 'auto' },
        }),
        proxiedTo: `/api/v1/agent/${AGENT}/chat`,
        sub: USER,
      },
      {
        name: 'an OAuth client-credentials token: the identity is the one the token acts as',
        request: () => ({ path: `/api/v1/agents/${AGENT}/conversations/stream`, token: clientCredentialsToken(), body: AGENT_TURN }),
        proxiedTo: `/api/v1/agent/${AGENT}/chat/stream`,
        sub: SERVICE_IDENTITY,
      },
      {
        name: 'Slack internal, the assistant, a workspace member with an account: the stand-in session Node mints',
        request: () => ({ path: '/api/v1/conversations/internal/stream', token: slackToken(SLACK_EMAIL, 'U0SAM'), body: { query: 'q', chatMode: 'agent' } }),
        proxiedTo: `/api/v1/agent/${PLACEHOLDER}/chat/stream`,
        sub: USER,
      },
      {
        name: 'Slack internal, non-streaming',
        request: () => ({ path: '/api/v1/conversations/internal/create', token: slackToken(SLACK_EMAIL, 'U0SAM'), body: { query: 'q', chatMode: 'internal_search' } }),
        proxiedTo: '/api/v1/chat',
        sub: USER,
        status: 201,
      },
      {
        name: 'Slack internal, an agent',
        request: () => ({ path: `/api/v1/agents/${AGENT}/conversations/internal/stream`, token: slackToken(SLACK_EMAIL, 'U0SAM'), body: AGENT_TURN }),
        proxiedTo: `/api/v1/agent/${AGENT}/chat/stream`,
        sub: USER,
      },
      {
        name: 'Slack internal, a service-account agent and a member without an account: the scoped token Node mints',
        request: () => ({ path: `/api/v1/agents/${AGENT}/conversations/internal/stream`, token: slackToken(SERVICE_EMAIL, 'U0BOTUSER'), body: AGENT_TURN }),
        proxiedTo: `/api/v1/agent/${AGENT}/chat/stream`,
        sub: '',
      },
    ];

    for (const site of sites) {
      it(site.name, async () => {
        rig = await startRig('on');
        const { path, token, body } = site.request(rig);
        const reply = await rig.send(path, token, body, FOREIGN_HEADER);
        const text = await reply.text();
        expect(reply.status, text).to.equal(site.status ?? 200);
        const calls = rig.chat();
        expect(calls.map((call) => call.path)).to.deep.equal([site.proxiedTo]);
        const turn = await verifiedAsTheQueryServiceWould(rig, calls[0]!);
        expect(turn.org).to.equal(ORG);
        if (site.sub !== '') expect(turn.sub).to.equal(site.sub);
        else expect(turn.sub, 'the scoped token’s own user, not the bot’s').to.match(/^[0-9a-f]{24}$/).and.not.equal(USER);
        expect(turn.cid).to.equal(calls[0]!.body['conversationId']);
        expect(calls[0]!.headers[RESOLVED_TURN_HEADER]).to.not.equal(FOREIGN_HEADER[RESOLVED_TURN_HEADER]);
      });
    }

    it('CTL-7: a classic choice the agent can honour is pinned as user_chose_classic, not as a durable route it did not take', async () => {
      rig = await startRig('on');
      const reply = await rig.send(`/api/v1/agents/${AGENT}/conversations/stream`, sessionToken(), { ...AGENT_TURN, chatMode: 'quick', harness: 'legacy' });
      await reply.text();
      const [call] = rig.chat();
      expect(call!.path).to.equal(`/api/v1/agent/${AGENT}/chat/stream`);
      expect((await verifiedAsTheQueryServiceWould(rig, call!)).pin.routing).to.deep.equal({ path: 'legacy', reason: 'user_chose_classic' });
    });

    it('a regenerate in Universal Agent Mode goes to the assistant’s agent route, and its header names no agent', async () => {
      rig = await startRig('on');
      const { conversationId, messageId } = seed(rig.store, false);
      const reply = await rig.send(`/api/v1/conversations/${conversationId}/message/${messageId}/regenerate`, sessionToken(), { chatMode: 'agent' });
      await reply.text();
      const [call] = rig.chat();
      expect(call!.path).to.equal(`/api/v1/agent/${PLACEHOLDER}/chat/stream`);
      expect(await verifiedAsTheQueryServiceWould(rig, call!)).to.include({ agent: null, cid: conversationId });
    });
  });

  describe('CPD10-28: under `on` a turn that cannot be pinned is refused, never proxied without a header', () => {
    const refusedWith503 = async (reply: Response): Promise<void> => {
      const body = (await reply.json()) as { error?: { code?: string } };
      expect(reply.status).to.equal(503);
      expect(body.error?.code).to.equal('HTTP_AGENT_HARNESS_RUN_CONFIG_UNAVAILABLE');
    };

    it('a streaming turn with no signing keys: 503 before anything is written, no conversation, no proxy', async () => {
      rig = await startRig('on', { signingKeys: false });
      await refusedWith503(await rig.send(`/api/v1/agents/${AGENT}/conversations/stream`, sessionToken(), AGENT_TURN));
      expect(rig.proxied).to.deep.equal([]);
      expect(rig.store.sessions).to.deep.equal([]);
    });

    it('a non-streaming turn whose heads cannot be read: 503, no proxy', async () => {
      rig = await startRig('on');
      rig.world.heads.unreadable = true;
      await refusedWith503(await rig.send('/api/v1/conversations/create', sessionToken(), { query: 'q', chatMode: 'internal_search' }));
      expect(rig.proxied).to.deep.equal([]);
    });

    it('a streaming turn whose route cannot be resolved: 503, no proxy (CPD10-7)', async () => {
      rig = await startRig('on');
      rig.world.heads.unreadable = true;
      await refusedWith503(await rig.send(`/api/v1/agents/${AGENT}/conversations/stream`, sessionToken(), AGENT_TURN));
      expect(rig.proxied).to.deep.equal([]);
      expect(rig.store.sessions).to.deep.equal([]);
    });

    it('shadow: the same unreadable control plane changes nothing: today’s loop serves the turn without a header', async () => {
      rig = await startRig('shadow');
      rig.world.heads.unreadable = true;
      const reply = await rig.send(`/api/v1/agents/${AGENT}/conversations/stream`, sessionToken(), AGENT_TURN);
      expect(reply.status).to.equal(200);
      await reply.text();
      expect(rig.chat()).to.have.length(1);
      expect(rig.chat()[0]!.headers).to.not.have.property(RESOLVED_TURN_HEADER);
    });
  });
});
