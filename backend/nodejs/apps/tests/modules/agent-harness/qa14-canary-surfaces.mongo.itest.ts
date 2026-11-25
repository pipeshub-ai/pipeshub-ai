/**
 * QA-14 QA14-N2..N4: a canary scan of every Node surface a durable run
 * reaches, through the composition `bindAgentHarness` builds on a real
 * MongoDB replica set: the session, internal and approvals routers at their
 * real prefixes behind the app's middleware order, the real admission,
 * claim, commit, gateway-token and follow-up paths, and the real OTLP
 * exporter posting to a local collector.
 *
 * Simulated: authentication (each bearer names an identity and is itself a
 * planted secret), the agent definition and the rollout, as the fault stack
 * does. The worker is this test, speaking the internal routes with the
 * worker credential and the attempt token Node minted.
 *
 * Planted: the callers' bearer tokens and Node's worker credential, attempt
 * and gateway tokens (secrets: on no surface, in no store); a vendor-shaped
 * key the person pasted into a tool argument (theirs to see, nobody else's);
 * the person's private text (on their own and their participants' views,
 * never on an operator surface).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import express, { NextFunction, Request, Response } from 'express';
import mongoose, { Types } from 'mongoose';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { Container } from 'inversify';
import sinon from 'sinon';
import { Logger } from '../../../src/libs/services/logger.service';
import { xssSanitizationMiddleware } from '../../../src/libs/middlewares/xss-sanitization.middleware';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { metricsBackend } from '../../../src/libs/services/telemetry/metrics-backend';
import {
  AGENT_HARNESS_AUTHORITY,
  bindAgentHarness,
  prepareAgentHarnessAuthority,
  prepareAgentHarnessStore,
  AGENT_HARNESS_CAPABILITIES,
} from '../../../src/modules/agent-harness/composition';
import { InMemoryConfigDocumentStore } from '../../../src/modules/agent-harness/adapters/in-memory-config-document-store';
import { InMemoryLiveBus } from '../../../src/modules/agent-harness/adapters/in-memory-live-bus';
import { ACTIVITY_LIVE_CHANNEL, ActivityFrame } from '../../../src/modules/agent-harness/projection/activity';
import {
  AttemptKeyring,
  PublishedAttemptKeys,
  WorkerCredentialSet,
} from '../../../src/modules/agent-harness/domain/authority';
import { HarnessAuthority } from '../../../src/modules/agent-harness/application/harness-authority';
import { AgentRunDefinition } from '../../../src/modules/agent-harness/domain/agent-definition';
import { decideChatModeRollout, HarnessRolloutDecision } from '../../../src/modules/agent-harness/domain/rollout';
import { HarnessRolloutPolicy } from '../../../src/modules/agent-harness/ports/harness-rollout';
import { ApproverDirectory } from '../../../src/modules/agent-harness/ports/approver-directory';
import {
  AGENT_HARNESS_INTERNAL_PREFIX,
  createAgentHarnessInternalRouter,
} from '../../../src/modules/agent-harness/transport/internal-routes';
import { createAgentHarnessSessionRouter } from '../../../src/modules/agent-harness/transport/session-routes';
import { HarnessChatSessionRepository } from '../../../src/libs/repositories/agent-harness/chat-session-repository/mongo';
import { HarnessConversationWriter } from '../../../src/libs/repositories/agent-harness/conversation-writer/mongo';
import { HarnessHistorySource } from '../../../src/libs/repositories/agent-harness/history-source/mongo';
import { HarnessConversationReader } from '../../../src/libs/repositories/agent-harness/conversation-reader/mongo';
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema';
import { connectHarnessMongo, disconnectHarnessMongo } from './support/harness-mongo';
import { CURRENT_WORKER_CAPABILITIES } from './support/worker-capabilities';
import { testKek, testRunKeys } from './support/data-protection';
import { assertNoCanary, canary, findCanaries, VECTOR_EXAMPLES, VECTOR_MASK } from './support/canary-scan';
import { canonicalJsonBytes } from '../../../src/modules/agent-harness/contracts/canonical-json';
import { inlineContentRef } from '../../../src/modules/agent-harness/domain/run-content';
import { bindingProof, ringBindingKey } from '../../../src/modules/agent-harness/domain/sandbox-binding';
import { ensureRigSecretKey } from './support/rig-secret-key';

const ORG = new Types.ObjectId().toHexString();
const OWNER = new Types.ObjectId().toHexString();
const PARTICIPANT = new Types.ObjectId().toHexString();
const APPROVER = new Types.ObjectId().toHexString();
const AGENT = 'agent-qa14';

const BEARERS = {
  owner: canary('owner-bearer'),
  participant: canary('participant-bearer'),
  approver: canary('approver-bearer'),
};
const IDENTITIES: Record<string, string> = {
  [BEARERS.owner]: OWNER,
  [BEARERS.participant]: PARTICIPANT,
  [BEARERS.approver]: APPROVER,
};
const PRIVATE = canary('private');
const PASTED = VECTOR_EXAMPLES.find((e) => e.kind === 'github_token')!.secret;
const ROGUE_FEATURE = canary('feature');

const definition: AgentRunDefinition = {
  agentKey: AGENT,
  definitionVersion: 1,
  models: [{ modelKey: 'model-1', modelName: 'scripted-x', provider: 'openAI', isReasoning: false }],
  defaultReasoningEffort: 'medium',
  toolsets: [],
  mcpServers: [],
  knowledge: [],
  skills: [],
  webSearch: null,
};
const ENABLED: HarnessRolloutDecision = { useHarness: true, harnessMajor: 2, reason: 'enabled' };

interface Collected {
  logs: unknown[][];
  spans: string[];
  spanArrived: Promise<void>;
}

interface Rig {
  base: string;
  liveBus: InMemoryLiveBus;
  activityBus: InMemoryLiveBus<ActivityFrame>;
  credential: string;
  /** The egress proxy's role credential: only it may ask for a gateway token (SEC-C-10). */
  egressCredential: string;
  collected: Collected;
  close(): Promise<void>;
}

async function startCollector(collected: Collected): Promise<{ url: string; server: Server }> {
  let arrived: () => void = () => undefined;
  collected.spanArrived = new Promise((resolve) => {
    arrived = resolve;
  });
  const app = express();
  app.use(express.text({ type: '*/*', limit: '10mb' }));
  app.post('*', (req, res) => {
    collected.spans.push(String(req.body));
    arrived();
    res.status(200).json({});
  });
  const server = await new Promise<Server>((resolve) => {
    const listening = app.listen(0, '127.0.0.1', () => resolve(listening));
  });
  return { url: `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`, server };
}

async function startRig(): Promise<Rig> {
  const restoreSecretKey = ensureRigSecretKey();
  const collected: Collected = { logs: [], spans: [], spanArrived: Promise.resolve() };
  const restore = sinon.createSandbox();
  for (const level of ['error', 'warn', 'info', 'debug'] as const) {
    restore.stub(Logger.prototype, level).callsFake((...args: unknown[]) => void collected.logs.push([level, ...args]));
  }
  const collector = await startCollector(collected);
  restore.stub(process, 'env').value({ ...process.env, HARNESS_OTEL: 'true', OTEL_EXPORTER_OTLP_ENDPOINT: collector.url });

  const logger = Logger.getInstance({ service: 'qa14-node' });
  const authenticate = (req: Request, res: Response, next: NextFunction): void => {
    const bearer = (req.headers.authorization ?? '').replace(/^Bearer /, '');
    const userId = IDENTITIES[bearer];
    if (userId === undefined) {
      res.status(401).json({ error: { message: 'unauthenticated' } });
      return;
    }
    (req as Request & { user: unknown }).user = { orgId: ORG, userId };
    next();
  };
  const liveBus = new InMemoryLiveBus();
  const activityBus = new InMemoryLiveBus<ActivityFrame>(4_000, ACTIVITY_LIVE_CHANNEL);
  const container = new Container();
  container.bind('Logger').toConstantValue(logger);
  container.bind('AuthMiddleware').toConstantValue({ authenticate });
  bindAgentHarness(container, {
    sessions: new HarnessChatSessionRepository(),
    conversations: new HarnessConversationWriter(),
    history: new HarnessHistorySource(),
    agentAccess: { authorizeRun: async () => definition },
    rollout: {
      decide: async (request: Parameters<HarnessRolloutPolicy['decide']>[0]) => decideChatModeRollout(ENABLED, request.model.chatMode, request.assistant === true),
      decideForOrg: async () => ({ useHarness: true, harnessMajor: 2, reason: 'enabled', resumeInFlightRuns: true }),
      // The Labs switches the pin routes on (CONTROL_PLANE_PIN is on by default, C4.04).
      settings: async () => ({ infrastructureReady: true, killSwitch: false, orgEnabled: true, modelFamilies: null }),
    },
    authority: {
      workerCredential: new InMemoryConfigDocumentStore<WorkerCredentialSet>(),
      // The egress proxy's own role: the gateway-token route takes only its credential (SEC-C-10).
      roleCredentials: {
        scrape: new InMemoryConfigDocumentStore<WorkerCredentialSet>(),
        'egress-proxy': new InMemoryConfigDocumentStore<WorkerCredentialSet>(),
        'sandbox-broker': new InMemoryConfigDocumentStore<WorkerCredentialSet>(),
      },
      attemptSigningKeys: new InMemoryConfigDocumentStore<AttemptKeyring>(),
      attemptPublicKeys: new InMemoryConfigDocumentStore<PublishedAttemptKeys>(),
    },
    gatewayTokens: {
      gatewaySigningKeys: new InMemoryConfigDocumentStore<AttemptKeyring>(),
      gatewayPublicKeys: new InMemoryConfigDocumentStore<PublishedAttemptKeys>(),
    },
    keyEncryptionKey: testKek(),
    runKeys: testRunKeys(),
    conversationReader: new HarnessConversationReader(),
    liveBus,
    activity: { setting: { enabled: async () => true }, bus: activityBus },
    approvers: {
      isApprover: async (_org: string, userId: string) => userId === APPROVER,
      isOrgAdmin: async () => false,
    } satisfies ApproverDirectory,
  } as never);
  await prepareAgentHarnessStore(logger, process.env, container.get(AGENT_HARNESS_CAPABILITIES));
  await prepareAgentHarnessAuthority(container, logger);
  const authority = container.get<HarnessAuthority>(AGENT_HARNESS_AUTHORITY);
  const credential = await authority.workerCredential();
  const egressCredential = await authority.credentialFor('egress-proxy');

  const app = express();
  app.use(express.json({ limit: '10mb' }));
  app.use(xssSanitizationMiddleware);
  app.use('/api/v1/agents', createAgentHarnessSessionRouter(container));
  app.use(AGENT_HARNESS_INTERNAL_PREFIX, createAgentHarnessInternalRouter(container));
  app.use(ErrorMiddleware.handleError());
  const server = await new Promise<Server>((resolve) => {
    const listening = app.listen(0, '127.0.0.1', () => resolve(listening));
  });
  return {
    base: `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`,
    liveBus,
    activityBus,
    credential: String(credential),
    egressCredential: String(egressCredential),
    collected,
    close: async () => {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
      await new Promise((resolve) => collector.server.close(resolve));
      restore.restore();
      restoreSecretKey();
    },
  };
}

interface Reply {
  status: number;
  text: string;
  json: any;
}

async function call(rig: Rig, method: string, path: string, bearer: string, body?: unknown): Promise<Reply> {
  const response = await fetch(`${rig.base}${path}`, {
    method,
    headers: { 'content-type': 'application/json', authorization: `Bearer ${bearer}` },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const text = await response.text();
  let json: unknown;
  try {
    json = JSON.parse(text);
  } catch {
    json = null;
  }
  return { status: response.status, text, json };
}

const worker = (type: string, data: Record<string, unknown>, visibility = 'owner') => ({
  type,
  schemaVersion: 1,
  critical: !type.startsWith('x.'),
  visibility,
  data,
});

describe('QA-14 canary scan of the Node surfaces (QA14-N2..N4)', function () {
  this.timeout(60_000);
  let rig: Rig;

  before(connectHarnessMongo);
  before(async () => {
    rig = await startRig();
  });

  after(async () => {
    await rig?.close();
    await disconnectHarnessMongo();
  });

  it('a run through admission, claim, approval, completion and follow-ups leaves no secret on any surface', async () => {
    const surfaces: Record<string, { value: unknown; audience: 'owner' | 'third' | 'operator' }> = {};
    const show = (name: string, value: unknown, audience: 'owner' | 'third' | 'operator') => {
      surfaces[name] = { value, audience };
    };

    const capabilities = { ...CURRENT_WORKER_CAPABILITIES, features: [...CURRENT_WORKER_CAPABILITIES.features, ROGUE_FEATURE] };
    // A worker polling for work is what makes the deployment admit durable runs.
    const idle = await call(rig, 'POST', `${AGENT_HARNESS_INTERNAL_PREFIX}/runs/claim`, rig.credential, {
      workerId: 'qa14-worker',
      leaseDurationMs: 60_000,
      role: 'dispatched',
      workerCapabilities: capabilities,
    });
    expect(idle.status, idle.text).to.be.oneOf([200, 204]);

    const admitted = await call(rig, 'POST', `/api/v1/agents/${AGENT}/runs`, BEARERS.owner, {
      query: `Open the ticket for ${PRIVATE}`,
      idempotencyKey: 'qa14-admit-1',
    });
    expect(admitted.status, admitted.text).to.equal(202);
    show('admission answer', admitted.json, 'owner');
    const runId = String(admitted.json.admission.run.runId);
    const sessionId = String(admitted.json.admission.sessionId);
    await ChatSession.updateOne(
      { _id: sessionId },
      { $set: { isShared: true, sharedWith: [{ userId: new Types.ObjectId(PARTICIPANT), accessLevel: 'read' }] } },
    );

    const refused = await call(rig, 'POST', `/api/v1/agents/${AGENT}/runs`, BEARERS.owner, {
      query: PRIVATE,
      idempotencyKey: 'qa14-admit-2',
      deadlineMs: PASTED,
      filters: { [PASTED]: PRIVATE },
    });
    expect(refused.status).to.equal(400);
    show('validation refusal', refused.json, 'operator');

    const claimed = await call(rig, 'POST', `${AGENT_HARNESS_INTERNAL_PREFIX}/runs/${runId}/claim`, rig.credential, {
      orgId: ORG,
      workerId: 'qa14-worker',
      leaseDurationMs: 60_000,
      workerCapabilities: capabilities,
    });
    expect(claimed.status, claimed.text).to.equal(200);
    const run = claimed.json.run as { attemptId: string; fence: number };
    const attemptToken = String(claimed.json.attemptToken.token);
    const gateway = await call(rig, 'POST', `${AGENT_HARNESS_INTERNAL_PREFIX}/runs/${runId}/gateway-token`, rig.egressCredential, {
      orgId: ORG,
      attemptId: run.attemptId,
      // As the broker signs a binding when it holds SECRET_KEY (R38IDENT-FU-7).
      bindingProof: bindingProof(ringBindingKey(process.env.SECRET_KEY ?? ''), { orgId: ORG, runId, attemptId: run.attemptId }),
    });
    expect(gateway.status, gateway.text).to.equal(200);
    const gatewayToken = String(gateway.json.token);
    const SECRETS = [...Object.values(BEARERS), rig.credential, rig.egressCredential, attemptToken, gatewayToken];

    const commit = async (commandId: string, events: unknown[], transition?: Record<string, unknown>) => {
      const reply = await call(rig, 'POST', `${AGENT_HARNESS_INTERNAL_PREFIX}/runs/${runId}/commit`, attemptToken, {
        commandId,
        attemptId: run.attemptId,
        fence: run.fence,
        events,
        ...(transition !== undefined ? { transition } : {}),
      });
      expect(reply.status, reply.text).to.equal(200);
    };
    const args = JSON.stringify({ title: PRIVATE, token: PASTED });
    await commit('qa14:1', [
      worker('turn.started', { turn: 1 }),
      worker('model.response', {
        messageRef: inlineContentRef(
          canonicalJsonBytes({ text: '', toolCalls: [{ id: 'c1', name: 'jira__create_issue', args: { title: PRIVATE, token: PASTED } }] }),
        ),
        toolCalls: [{ callId: 'c1', modelName: 'jira__create_issue', argsDigest: `sha256:${'c'.repeat(64)}` }],
        usage: { inputTokens: 0, outputTokens: 0 },
        provider: 'p',
        transport: 't',
      }),
      worker('x.pipeshub.message.presented', { text: `Working on ${PRIVATE}` }),
      worker('x.pipeshub.tool.call_presented', {
        callId: 'c1',
        toolName: 'jira__create_issue',
        argsJson: args,
        argsSummary: `Create “${PRIVATE}” with ${PASTED}`,
        displayName: 'Create a Jira issue',
      }),
      worker('interaction.requested', {
        interactionId: 'ix.approve.t1.0',
        kind: 'approval',
        bindingDigest: `sha256:${'a'.repeat(64)}`,
      }),
    ]);
    rig.liveBus.publish({ runId, fence: run.fence, modelAttempt: 0, n: 1, kind: 'text_delta', data: { text: PRIVATE } } as never);
    rig.activityBus.publish({
      runId,
      fence: run.fence,
      modelAttempt: 0,
      n: 1,
      kind: 'command_started',
      data: { commandId: 'e1', source: 'engine', command: `grep ${PRIVATE}`, startedAt: 1, attemptId: run.attemptId },
    });

    const path = `/api/v1/agents/${AGENT}/conversations/${sessionId}/runs/${runId}`;
    show('timeline (owner, waiting)', (await call(rig, 'GET', `${path}/timeline`, BEARERS.owner)).json, 'owner');
    const activity = await fetch(`${rig.base}${path}/activity`, { headers: { authorization: `Bearer ${BEARERS.owner}` } });
    const reader = activity.body!.getReader();
    const firstChunk = await reader.read();
    await reader.cancel();
    show('activity snapshot (owner)', new TextDecoder().decode(firstChunk.value), 'owner');

    const decided = await call(rig, 'POST', `${path}/interactions/ix.approve.t1.0/decision`, BEARERS.approver, {
      decision: 'denied',
      idempotencyKey: 'qa14-deny-1',
    });
    expect(decided.status, decided.text).to.equal(200);
    show('decision answer', decided.json, 'third');

    const ownerStream = fetch(`${rig.base}${path}/stream`, { headers: { authorization: `Bearer ${BEARERS.owner}` } }).then((r) => r.text());
    const sharedStream = fetch(`${rig.base}${path}/stream`, { headers: { authorization: `Bearer ${BEARERS.participant}` } }).then((r) => r.text());
    await commit(
      'qa14:2',
      [
        worker('x.pipeshub.message.presented', { text: `Done with ${PRIVATE}` }),
        worker('x.pipeshub.answer.finalized', {
          outcome: 'answered',
          answer: `Done with ${PRIVATE}`,
          partTexts: [`Done with ${PRIVATE}`],
          citations: [],
          confidence: null,
          label: { complete: true, sources: [] },
        }),
      ],
      { status: 'completed', outcome: 'answer' },
    );
    show('SSE stream (owner)', await ownerStream, 'owner');
    show('SSE stream (participant)', await sharedStream, 'third');
    const offered = await call(rig, 'POST', `${AGENT_HARNESS_INTERNAL_PREFIX}/runs/${runId}/followups`, attemptToken, {
      attemptId: run.attemptId,
      fence: run.fence,
      items: [{ text: `More about ${PRIVATE}?` }],
    });
    expect(offered.status, offered.text).to.equal(200);
    show('follow-up offer answer (to the worker)', offered.json, 'owner');
    show('follow-ups (owner)', (await call(rig, 'GET', `${path}/followups`, BEARERS.owner)).json, 'owner');
    show('timeline (owner, done)', (await call(rig, 'GET', `${path}/timeline`, BEARERS.owner)).json, 'owner');

    await rig.collected.spanArrived;
    show('exported spans', rig.collected.spans, 'operator');
    show('Node logs', rig.collected.logs, 'operator');
    show('metrics', await metricsBackend.serialize(), 'operator');
    show('dispatch outbox', await mongoose.connection.db!.collection('outbox_events').find({}).toArray(), 'operator');

    // The rig reached every surface: each holds what its audience should see.
    const text = (name: string) => JSON.stringify(surfaces[name]!.value);
    // The approvals queue left this tree with WP-130: the owner's waiting timeline is where the held call shows.
    expect(text('timeline (owner, waiting)'), 'the owner sees the held call').to.include('ix.approve.t1.0').and.include(PRIVATE);
    expect(text('validation refusal'), 'a pasted secret is masked in the refusal').to.include(VECTOR_MASK);
    for (const name of ['SSE stream (owner)', 'SSE stream (participant)', 'activity snapshot (owner)', 'follow-ups (owner)', 'timeline (owner, done)']) {
      expect(text(name), name).to.include(PRIVATE);
    }
    expect(text('SSE stream (owner)'), 'the owner sees what they pasted').to.include(PASTED);
    expect(text('exported spans')).to.include('pipeshub.run.admit');
    expect(text('metrics')).to.include('pipeshub_agent_chat_turns_total');
    expect(surfaces['dispatch outbox']!.value as unknown[]).to.not.have.length(0);
    expect(surfaces['Node logs']!.value as unknown[]).to.not.have.length(0);

    const leaks: Record<string, string[]> = {};
    for (const [name, { value, audience }] of Object.entries(surfaces)) {
      const found = findCanaries(value, {
        secrets: SECRETS,
        ...(audience === 'operator' ? { privates: [PRIVATE, ROGUE_FEATURE] } : {}),
        shapes: audience === 'owner' ? 'none' : 'all',
      });
      if (found.length > 0) leaks[name] = found;
    }
    expect(leaks).to.deep.equal({});

    const stores: Record<string, unknown[]> = {};
    for (const { name } of await mongoose.connection.db!.listCollections({}, { nameOnly: true }).toArray()) {
      stores[name] = await mongoose.connection.db!.collection(name).find({}).toArray();
    }
    assertNoCanary('the Mongo stores', JSON.stringify(stores), { secrets: SECRETS, shapes: 'none' });
  });
});
