/**
 * QA-26 rig: the harness composition `es.container.ts` builds, on a real
 * MongoDB replica set, with every tenant-facing router mounted at its real
 * prefix behind the app's middleware order, and two orgs seeded through the
 * product's own routes.
 *
 * Real: the stores (Mongo), the configuration documents and the model-account
 * vault (the production adapters over an in-memory KV), the admin check and
 * the schedule actor (the users and orgs collections), the conversation
 * module's adapters, the worker credential, attempt and gateway tokens.
 * Simulated: authentication (each bearer names one identity), the agent
 * authority (each org has one agent), the query service behind publications
 * (it records the credential it is handed), the toolset store behind a chat's
 * repository (each org has one GitHub connection), the artifact service and
 * the object store.
 */
import { HarnessChatTitles, HarnessMemberProfiles } from '../../../../src/libs/repositories/agent-harness/audit-names/mongo';
import { FakeChatAttachments } from './chat-attachments';
import { ChainWorkspaceDownloadAudit } from '../../../../src/modules/agent-harness/adapters/chain-workspace-download-audit';
import { inMongoTransaction } from '../../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { MongoAuditChainStore } from '../../../../src/libs/repositories/agent-harness/audit/mongo';
import { WORKSPACE_DOWNLOADED_KIND } from '../../../../src/modules/agent-harness/domain/workspace-download-audit';
import express, { NextFunction, Request, Response } from 'express';
import mongoose, { Types } from 'mongoose';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { Container } from 'inversify';
import sinon from 'sinon';
import { Logger } from '../../../../src/libs/services/logger.service';
import { requestContextMiddleware } from '../../../../src/libs/middlewares/request.context';
import { xssSanitizationMiddleware } from '../../../../src/libs/middlewares/xss-sanitization.middleware';
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_CAPABILITIES,
  bindAgentHarness,
  AGENT_HARNESS_MEMORY_STORE,
  AGENT_HARNESS_SESSION_REPOSITORIES,
  prepareAgentHarnessAuthority,
  prepareAgentHarnessStore,
} from '../../../../src/modules/agent-harness/composition';
import {
  createApprovalsStore,
  createGatewayTokenStores,
  createHarnessAuthorityStores,
  createMemoryPolicyStore,
} from '../../../../src/modules/agent-harness/adapters/configuration/encrypted-config-document-store';
import { ModelAccountVault } from '../../../../src/modules/agent-harness/adapters/configuration/model-account-vault';
import { InMemoryLiveBus } from '../../../../src/modules/agent-harness/adapters/in-memory-live-bus';
import { ACTIVITY_LIVE_CHANNEL, ActivityFrame } from '../../../../src/modules/agent-harness/projection/activity';
import { HarnessAuthority } from '../../../../src/modules/agent-harness/application/harness-authority';
import { AgentRunDefinition } from '../../../../src/modules/agent-harness/domain/agent-definition';
import { decideChatModeRollout, HarnessRolloutDecision } from '../../../../src/modules/agent-harness/domain/rollout';
import { MemoryEntry } from '../../../../src/modules/agent-harness/domain/memory';
import { MemoryStore } from '../../../../src/modules/agent-harness/ports/memory-store';
import { HarnessRolloutPolicy } from '../../../../src/modules/agent-harness/ports/harness-rollout';
import { AGENT_HARNESS_INTERNAL_PREFIX } from '../../../../src/modules/agent-harness/transport/internal-routes';
import { HarnessChatSessionRepository } from '../../../../src/libs/repositories/agent-harness/chat-session-repository/mongo';
import { MongoCodingEnginePreferences } from '../../../../src/libs/repositories/agent-harness/coding-engine-preferences/mongo';
import { SessionRepositoryService } from '../../../../src/modules/agent-harness/application/session-repository-service';
import { HarnessConversationWriter } from '../../../../src/libs/repositories/agent-harness/conversation-writer/mongo';
import { HarnessHistorySource } from '../../../../src/libs/repositories/agent-harness/history-source/mongo';
import { HarnessConversationReader } from '../../../../src/libs/repositories/agent-harness/conversation-reader/mongo';
import { HarnessApproverDirectory } from '../../../../src/libs/repositories/agent-harness/approver-directory/mongo';
import { HarnessActorAuthority } from '../../../../src/libs/repositories/agent-harness/actor-authority/mongo';
import { HarnessSessionRoles } from '../../../../src/libs/repositories/agent-harness/session-roles/mongo';
import { HarnessSessionForkStore } from '../../../../src/libs/repositories/agent-harness/session-forks/mongo';
import { HarnessConversationEraser } from '../../../../src/libs/repositories/agent-harness/conversation-eraser/mongo';
import { HarnessConversationRetention } from '../../../../src/libs/repositories/agent-harness/conversation-retention/mongo';
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import { canonicalJsonBytes } from '../../../../src/modules/agent-harness/contracts/canonical-json';
import { inlineContentRef } from '../../../../src/modules/agent-harness/domain/run-content';
import { MemoryKv, platformCipher } from './model-accounts';
import { InMemoryConfiguredModels } from './model-access';
import { CURRENT_WORKER_CAPABILITIES } from './worker-capabilities';
import { testKek, testRunKeys } from './data-protection';
import { Caller, HARNESS_SURFACES, TenantIds } from './cross-tenant-routes';
import { CONTROL_PLANE_MEMBERSHIP } from '../../../../src/modules/control-plane';
import { InMemoryMembership } from '../../control-plane/support/in-memory-services';
import { continueAs } from '../../../helpers/request-org-context';
import { bindingProof, ringBindingKey } from '../../../../src/modules/agent-harness/domain/sandbox-binding';
import { ensureRigSecretKey } from './rig-secret-key';

export type Role = 'owner' | 'peer' | 'admin' | 'member' | 'orgAdmin';

/** One org's people: org A has an owner, a second member (the peer) and an admin; org B a member and an admin. */
export interface Tenant {
  name: 'A' | 'B';
  orgId: string;
  agentKey: string;
  owner: string;
  peer: string;
  admin: string;
  /** Text only this tenant's owner wrote or was answered: on no other caller's surface. */
  privateText: string;
}

export interface SeededTenant extends Tenant {
  ids: TenantIds;
  attemptToken: string;
  gatewayToken: string;
  /** The schedule's own webhook secret. */
  webhookToken: string;
  /** What the owner and the admin can read: every id and text the other callers must never see. */
  canaries: string[];
}

export interface Reply {
  status: number;
  text: string;
  contentType: string;
}

export interface CrossTenantRig {
  base: string;
  credential: string;
  /** The egress proxy's role credential: only it may ask for a gateway token (SEC-C-10). */
  egressCredential: string;
  kv: MemoryKv;
  /** Authorizations the publication authority (the query service) was handed, in order. */
  publicationCalls: string[];
  bearer(userId: string): string;
  call(method: string, path: string, authorization: string | null, body?: unknown, headers?: Record<string, string>): Promise<Reply>;
  /** Opens an SSE route and reads its first chunk; the status says whether it opened. */
  open(path: string, authorization: string): Promise<Reply>;
  mountedSurfaces(): Container;
  close(): Promise<void>;
}

/** The one GitHub connection an org's people may use (the toolset store, simulated). */
const githubConnection = (orgId: string): string => `github-${orgId}`;

const ENABLED: HarnessRolloutDecision = { useHarness: true, harnessMajor: 2, reason: 'enabled' };

const definition = (agentKey: string): AgentRunDefinition => ({
  agentKey,
  definitionVersion: 1,
  models: [{ modelKey: 'model-1', modelName: 'scripted-x', provider: 'openAI', isReasoning: false }],
  defaultReasoningEffort: 'medium',
  toolsets: [],
  mcpServers: [],
  knowledge: [],
  skills: [],
  webSearch: null,
});

export function tenant(name: 'A' | 'B'): Tenant {
  const id = () => new Types.ObjectId().toHexString();
  return {
    name,
    orgId: id(),
    agentKey: `agent-qa26-${name.toLowerCase()}`,
    owner: id(),
    peer: id(),
    admin: id(),
    privateText: `qa26-private-${name}-${id()}`,
  };
}

/** The org, its people and their roles, in the collections the admin check and the schedule actor read. */
async function seedPeople(t: Tenant): Promise<void> {
  const db = mongoose.connection.db!;
  await db.collection('org').insertOne({
    _id: new Types.ObjectId(t.orgId),
    registeredName: `qa26 org ${t.name}`,
    domain: `qa26-${t.name.toLowerCase()}.example.com`,
    contactEmail: `admin@qa26-${t.name.toLowerCase()}.example.com`,
    accountType: 'business',
    slug: `qa26-org-${t.orgId}`,
    isDeleted: false,
  } as never);
  const people: Array<[string, string]> = [
    [t.owner, 'member'],
    [t.peer, 'member'],
    [t.admin, 'admin'],
  ];
  await db.collection('users').insertMany(
    people.map(([userId, role]) => ({
      _id: new Types.ObjectId(userId),
      orgId: new Types.ObjectId(t.orgId),
      email: `${userId}@qa26-${t.name.toLowerCase()}.example.com`,
      fullName: `qa26 ${t.name} ${role}`,
      role,
      isDeleted: false,
      hasLoggedIn: true,
      slug: `qa26-${userId}`,
    })) as never,
  );
}

export async function startCrossTenantRig(tenants: readonly Tenant[]): Promise<CrossTenantRig> {
  const restoreSecretKey = ensureRigSecretKey();
  const restore = sinon.createSandbox();
  for (const level of ['error', 'warn', 'info', 'debug'] as const) restore.stub(Logger.prototype, level);
  const logger = Logger.getInstance({ service: 'qa26-node' });
  const identities = new Map<string, Caller>();
  for (const t of tenants) {
    for (const userId of [t.owner, t.peer, t.admin]) identities.set(`qa26.${userId}`, { orgId: t.orgId, userId });
    await seedPeople(t);
  }
  const agents = new Map(tenants.map((t) => [t.orgId, t.agentKey]));
  const authenticate = (req: Request, res: Response, next: NextFunction): void => {
    const identity = identities.get((req.headers.authorization ?? '').replace(/^Bearer /, ''));
    if (identity === undefined) {
      res.status(401).json({ error: { message: 'unauthenticated' } });
      return;
    }
    (req as Request & { user: unknown }).user = { ...identity };
    continueAs(identity, next);
  };

  const kv = new MemoryKv();
  const store = kv as never;
  const approvals = createApprovalsStore(store, platformCipher);
  const approverDirectory = new HarnessApproverDirectory();
  const publicationCalls: string[] = [];
  const publication = (authorization: string) => {
    publicationCalls.push(authorization);
    return Promise.resolve({ ok: false as const, status: 404, code: 'not_found', message: 'Not found' });
  };
  const container = new Container();
  container.bind('Logger').toConstantValue(logger);
  const refuseScoped = () => (_req: Request, res: Response): void => {
    res.status(401).json({ error: { message: 'no service token in this rig' } });
  };
  container.bind('AuthMiddleware').toConstantValue({ authenticate, scopedTokenValidator: refuseScoped });
  // The pin (on by default, C4.04) reads team membership; the rig's users are in no team.
  container.bind(CONTROL_PLANE_MEMBERSHIP).toConstantValue(new InMemoryMembership());
  container.bind('AppConfig').toConstantValue({
    aiBackend: 'http://127.0.0.1:9',
    jwtSecret: 'qa26-jwt',
    scopedJwtSecret: 'qa26-scoped',
    storageBackend: 'http://127.0.0.1:9',
    storage: { storageType: 'local' },
  });
  bindAgentHarness(container, {
    sessions: new HarnessChatSessionRepository(),
    conversations: new HarnessConversationWriter(),
    history: new HarnessHistorySource(),
    agentAccess: {
      authorizeRun: async ({ orgId, agentKey }: { orgId: string; agentKey: string }) => (agents.get(orgId) === agentKey ? definition(agentKey) : null),
    },
    rollout: {
      decide: async (request: Parameters<HarnessRolloutPolicy['decide']>[0]) =>
        decideChatModeRollout(ENABLED, request.model.chatMode, request.assistant === true),
      decideForOrg: async () => ({ useHarness: true, harnessMajor: 2, reason: 'enabled', resumeInFlightRuns: true }),
      // The Labs switches the pin routes on (CONTROL_PLANE_PIN is on by default, C4.04).
      settings: async () => ({ infrastructureReady: true, killSwitch: false, orgEnabled: true, modelFamilies: null }),
    },
    authority: createHarnessAuthorityStores(store, platformCipher),
    gatewayTokens: createGatewayTokenStores(store, platformCipher),
    keyEncryptionKey: testKek(),
    runKeys: testRunKeys(),
    conversationReader: new HarnessConversationReader(),
    roles: new HarnessSessionRoles(),
    collaboratorTurns: { enabled: async () => true },
    liveBus: new InMemoryLiveBus(),
    activity: { setting: { enabled: async () => true }, bus: new InMemoryLiveBus<ActivityFrame>(4_000, ACTIVITY_LIVE_CHANNEL) },
    approvers: approverDirectory,
    orgMembers: approverDirectory,
    auditNames: { members: new HarnessMemberProfiles(), chats: new HarnessChatTitles() },
    approvalSettings: { document: approvals },
    memory: { policies: createMemoryPolicyStore(store, platformCipher) },
    modelAccounts: new ModelAccountVault(kv, platformCipher),
    modelAccess: { models: new InMemoryConfiguredModels() },
    codingEnginePreferences: new MongoCodingEnginePreferences(),
    repositoryToolsets: {
      usableGithubInstance: async ({ orgId, instanceId }: { orgId: string; instanceId: string }) => instanceId === githubConnection(orgId),
    },
    publications: {
      setting: { enabled: async () => true },
      authority: { create: publication, list: publication, revoke: publication },
    },
    schedules: { actors: new HarnessActorAuthority(() => 'qa26-jwt'), setting: { enabled: async () => true } },
    forks: {
      store: new HarnessSessionForkStore(),
      artifacts: { copy: async () => new Map() },
      objects: { get: async () => null, put: async () => undefined },
    },
    erasure: {
      conversations: new HarnessConversationEraser(new FakeChatAttachments()),
      artifacts: { eraseSession: async () => 0, eraseRuns: async () => 0 },
      liveBus: null,
      retention: new HarnessConversationRetention(),
      agentDeletions: { committed: async () => false },
    },
  } as never);
  await prepareAgentHarnessStore(logger, process.env, container.get(AGENT_HARNESS_CAPABILITIES));
  await prepareAgentHarnessAuthority(container, logger);
  const authority = container.get<HarnessAuthority>(AGENT_HARNESS_AUTHORITY);
  const credential = String(await authority.workerCredential());
  const egressCredential = String(await authority.credentialFor('egress-proxy'));

  const app = express();
  app.use(requestContextMiddleware);
  app.use(express.json({ limit: '10mb' }));
  app.use(xssSanitizationMiddleware);
  for (const surface of HARNESS_SURFACES) app.use(surface.prefix, surface.build(container));
  app.use(ErrorMiddleware.handleError());
  const server = await new Promise<Server>((resolve) => {
    const listening = app.listen(0, '127.0.0.1', () => resolve(listening));
  });
  const base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;

  const call: CrossTenantRig['call'] = async (method, path, authorization, body, headers = {}) => {
    const response = await fetch(`${base}${path}`, {
      method,
      headers: {
        'content-type': 'application/json',
        ...(authorization === null ? {} : { authorization: `Bearer ${authorization}` }),
        ...headers,
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    return { status: response.status, text: await response.text(), contentType: response.headers.get('content-type') ?? '' };
  };

  return {
    base,
    credential,
    egressCredential,
    kv,
    publicationCalls,
    bearer: (userId) => `qa26.${userId}`,
    call,
    open: async (path, authorization) => {
      const response = await fetch(`${base}${path}`, { headers: { authorization: `Bearer ${authorization}` } });
      const contentType = response.headers.get('content-type') ?? '';
      if (!contentType.includes('text/event-stream')) {
        return { status: response.status, text: await response.text(), contentType };
      }
      const reader = response.body!.getReader();
      const first = await reader.read();
      await reader.cancel();
      return { status: response.status, text: first.done ? '' : new TextDecoder().decode(first.value), contentType };
    },
    mountedSurfaces: () => container,
    close: async () => {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
      restore.restore();
      restoreSecretKey();
    },
  };
}

const worker = (type: string, data: Record<string, unknown>) => ({
  type,
  schemaVersion: 1,
  critical: !type.startsWith('x.'),
  visibility: 'owner',
  data,
});

const json = (reply: Reply): any => JSON.parse(reply.text);

function expectStatus(reply: Reply, status: number[], what: string): void {
  if (!status.includes(reply.status)) throw new Error(`${what}: ${String(reply.status)} ${reply.text.slice(0, 400)}`);
}

interface Claimed {
  runId: string;
  attemptId: string;
  fence: number;
  attemptToken: string;
}

async function claim(rig: CrossTenantRig, t: Tenant, runId: string): Promise<Claimed> {
  const claimed = await rig.call('POST', `${AGENT_HARNESS_INTERNAL_PREFIX}/runs/${runId}/claim`, rig.credential, {
    orgId: t.orgId,
    workerId: `qa26-worker-${t.name}`,
    leaseDurationMs: 300_000,
    workerCapabilities: CURRENT_WORKER_CAPABILITIES,
  });
  expectStatus(claimed, [200], `claim ${runId}`);
  const body = json(claimed);
  return { runId, attemptId: body.run.attemptId, fence: body.run.fence, attemptToken: String(body.attemptToken.token) };
}

async function commit(
  rig: CrossTenantRig,
  run: Claimed,
  commandId: string,
  events: unknown[],
  transition?: Record<string, unknown>,
): Promise<void> {
  const reply = await rig.call('POST', `${AGENT_HARNESS_INTERNAL_PREFIX}/runs/${run.runId}/commit`, run.attemptToken, {
    commandId,
    attemptId: run.attemptId,
    fence: run.fence,
    events,
    ...(transition !== undefined ? { transition } : {}),
  });
  expectStatus(reply, [200], `commit ${commandId}`);
}

async function firstMessageId(sessionId: string): Promise<string> {
  const message = await ChatSessionMessage.findOne({ sessionId }).sort({ _id: 1 }).lean();
  if (message === null) throw new Error(`no message in ${sessionId}`);
  return String(message._id);
}

/**
 * Seeds one org through the product's routes: an agent run waiting on an
 * approval and an unknown effect, with a finished command; a finished chat
 * run with follow-ups; a webhook schedule; a budget reservation; a GitHub repository on each chat; and (through the memory store,
 * as a worker's proposal lands) a user and an agent memory.
 */
export async function seedTenant(rig: CrossTenantRig, t: Tenant): Promise<SeededTenant> {
  const owner = rig.bearer(t.owner);
  const tag = t.name.toLowerCase();

  const idle = await rig.call('POST', `${AGENT_HARNESS_INTERNAL_PREFIX}/runs/claim`, rig.credential, {
    workerId: `qa26-idle-${tag}`,
    leaseDurationMs: 60_000,
    role: 'dispatched',
    workerCapabilities: CURRENT_WORKER_CAPABILITIES,
  });
  expectStatus(idle, [200, 204], 'idle claim');

  const agentAdmission = await rig.call('POST', `/api/v1/agents/${t.agentKey}/runs`, owner, {
    query: `Agent turn: ${t.privateText}`,
    idempotencyKey: `qa26-agent-${tag}`,
  });
  expectStatus(agentAdmission, [202], 'agent admission');
  const agentRunId = String(json(agentAdmission).admission.run.runId);
  const agentSessionId = String(json(agentAdmission).admission.sessionId);
  const agentRun = await claim(rig, t, agentRunId);
  const gateway = await rig.call('POST', `${AGENT_HARNESS_INTERNAL_PREFIX}/runs/${agentRunId}/gateway-token`, rig.egressCredential, {
    orgId: t.orgId,
    attemptId: agentRun.attemptId,
    // As the broker signs a binding when it holds SECRET_KEY (R38IDENT-FU-7).
    bindingProof: bindingProof(ringBindingKey(process.env.SECRET_KEY ?? ''), {
      orgId: t.orgId, runId: agentRunId, attemptId: agentRun.attemptId,
    }),
  });
  expectStatus(gateway, [200], 'gateway token');
  const commandId = `cmd-${tag}`;
  const finished = worker('x.pipeshub.command.finished', {
    commandId,
    source: 'run_code',
    command: `grep ${t.privateText}`,
    status: 'completed',
    exitCode: 0,
    startedAt: 1_759_320_000_000,
    durationMs: 1,
    stdout: { tail: t.privateText, bytes: t.privateText.length, truncated: false },
    stderr: { tail: '', bytes: 0, truncated: false },
    liveDropped: 0,
    log: inlineContentRef(Buffer.from(`log of ${t.privateText}`)),
  });
  // Long enough to be a canary of its own: a short id turns up inside unrelated request ids.
  const toolCallId = `call-qa26-${tag}`;
  const interactionId = 'ix.approve.t1.0';
  const unknownInteractionId = 'ix.unknown.t1.1';
  await commit(rig, agentRun, `qa26-${tag}:1`, [
    worker('turn.started', { turn: 1 }),
    worker('model.response', {
      messageRef: inlineContentRef(canonicalJsonBytes({ text: '', toolCalls: [{ id: toolCallId, name: 'jira__create_issue', args: { title: t.privateText } }] })),
      toolCalls: [{ callId: toolCallId, modelName: 'jira__create_issue', argsDigest: `sha256:${'c'.repeat(64)}` }],
      usage: { inputTokens: 0, outputTokens: 0 },
      provider: 'p',
      transport: 't',
    }),
    worker('x.pipeshub.message.presented', { text: `Working on ${t.privateText}` }),
    worker('x.pipeshub.tool.call_presented', {
      callId: toolCallId,
      toolName: 'jira__create_issue',
      argsJson: JSON.stringify({ title: t.privateText }),
      argsSummary: `Create ${t.privateText}`,
      displayName: 'Create a Jira issue',
    }),
    finished,
    worker('interaction.requested', { interactionId, kind: 'approval', bindingDigest: `sha256:${'a'.repeat(64)}` }),
    worker('interaction.requested', { interactionId: unknownInteractionId, kind: 'approval', bindingDigest: `sha256:${'b'.repeat(64)}` }),
  ]);
  const reservation = await rig.call('POST', `${AGENT_HARNESS_INTERNAL_PREFIX}/runs/${agentRunId}/budget/reservations`, agentRun.attemptToken, {
    key: `res-${tag}`,
    requested: { usdMicros: 1, tokens: 1 },
    minimum: { usdMicros: 1, tokens: 1 },
    ttlS: 600,
  });
  // One charged call, so every spend reader has the tenant's person, agent and chat to leak.
  const charged = await rig.call('POST', `${AGENT_HARNESS_INTERNAL_PREFIX}/runs/${agentRunId}/budget/reservations`, agentRun.attemptToken, {
    key: `cost-${tag}`,
    requested: { usdMicros: 2, tokens: 2 },
    minimum: { usdMicros: 1, tokens: 1 },
    ttlS: 600,
  });
  expectStatus(charged, [200], 'charged reservation');
  const settled = await rig.call(
    'POST',
    `${AGENT_HARNESS_INTERNAL_PREFIX}/runs/${agentRunId}/budget/reservations/cost-${tag}/commit`,
    agentRun.attemptToken,
    { actual: { usdMicros: 2, tokens: 2 } },
  );
  expectStatus(settled, [200], 'charged commit');

  const chatAdmission = await rig.call('POST', '/api/v1/conversations/runs', owner, {
    query: `Chat turn: ${t.privateText}`,
    chatMode: 'internal_search',
    idempotencyKey: `qa26-chat-${tag}`,
  });
  expectStatus(chatAdmission, [202], 'chat admission');
  const chatRunId = String(json(chatAdmission).admission.run.runId);
  const chatSessionId = String(json(chatAdmission).admission.sessionId);
  const chatRun = await claim(rig, t, chatRunId);
  await commit(
    rig,
    chatRun,
    `qa26-${tag}:chat`,
    [
      worker('turn.started', { turn: 1 }),
      finished,
      worker('x.pipeshub.message.presented', { text: `Answer about ${t.privateText}` }),
      worker('x.pipeshub.answer.finalized', {
        outcome: 'answered',
        answer: `Answer about ${t.privateText}`,
        partTexts: [`Answer about ${t.privateText}`],
        citations: [],
        confidence: null,
        label: { complete: true, sources: [] },
      }),
    ],
    { status: 'completed', outcome: 'answer' },
  );
  const offered = await rig.call('POST', `${AGENT_HARNESS_INTERNAL_PREFIX}/runs/${chatRunId}/followups`, chatRun.attemptToken, {
    attemptId: chatRun.attemptId,
    fence: chatRun.fence,
    items: [{ text: `More about ${t.privateText}?` }],
  });
  expectStatus(offered, [200], 'follow-ups');

  const schedule = await rig.call('POST', '/api/v1/agent-harness/schedules/', owner, {
    name: `qa26 schedule ${t.privateText}`,
    agentKey: t.agentKey,
    input: { query: `Scheduled: ${t.privateText}` },
    trigger: { kind: 'webhook' },
  });
  expectStatus(schedule, [201], 'schedule');

  // GIT-FLOW: each chat works on a repository, saved as admission saves a new chat's (its name is a canary).
  const repositories = rig.mountedSurfaces().get<SessionRepositoryService>(AGENT_HARNESS_SESSION_REPOSITORIES);
  for (const sessionId of [agentSessionId, chatSessionId]) {
    await repositories.saveNewChat(t.orgId, t.owner, sessionId, {
      url: `https://github.com/qa26-${tag}/${t.privateText}`,
      toolsetInstanceId: githubConnection(t.orgId),
    });
  }

  const memories = rig.mountedSurfaces().get<MemoryStore>(AGENT_HARNESS_MEMORY_STORE);
  const memory = (memoryId: string, scope: MemoryEntry['scope'], content: string): MemoryEntry => ({
    memoryId,
    orgId: t.orgId,
    scope,
    ownerId: t.owner,
    agentKey: scope === 'agent' ? t.agentKey : null,
    sessionId: agentSessionId,
    runId: agentRunId,
    origin: 'model',
    kind: 'fact',
    content,
    status: 'pending',
    label: null,
    createdAt: 1,
    updatedAt: 1,
    reviewedBy: null,
    reviewedAt: null,
  });
  await memories.propose(memory(`mem-user-${tag}`, 'user', `Remembers ${t.privateText}`));
  await memories.propose(memory(`mem-agent-${tag}`, 'agent', `Agent remembers ${t.privateText}`));
  // AUDITUI: a download on the org's chain, so the download-audit routes have the org's records to keep.
  await new ChainWorkspaceDownloadAudit(new MongoAuditChainStore(), inMongoTransaction).record({
    type: WORKSPACE_DOWNLOADED_KIND,
    orgId: t.orgId,
    actorId: t.owner,
    sessionId: chatSessionId,
    revision: 1,
    target: 'file',
    path: `notes/${t.privateText}.md`,
    files: 1,
    bytes: 42,
  });

  const ids: TenantIds = {
    orgId: t.orgId,
    ownerId: t.owner,
    agentKey: t.agentKey,
    agentSessionId,
    agentRunId,
    agentMessageId: await firstMessageId(agentSessionId),
    chatSessionId,
    chatRunId,
    chatMessageId: await firstMessageId(chatSessionId),
    interactionId,
    commandId,
    toolCallId,
    workspaceId: `ws-${tag}`,
    revision: '1',
    reservationKey: reservation.status === 200 ? `res-${tag}` : `res-${tag}-unreserved`,
    scheduleId: String(json(schedule).schedule.id),
    memoryId: `mem-user-${tag}`,
    agentMemoryId: `mem-agent-${tag}`,
    publicationId: `pub-${tag}`,
    unknownRunId: agentRunId,
    unknownInteractionId,
  };
  const canaries = [
    t.privateText,
    ...Object.entries(ids)
      .filter(([name]) => !['orgId', 'revision', 'interactionId', 'unknownInteractionId'].includes(name))
      .map(([, value]) => value),
  ];
  return {
    ...t,
    ids,
    attemptToken: agentRun.attemptToken,
    gatewayToken: String(json(gateway).token),
    webhookToken: String(json(schedule).webhookToken),
    canaries,
  };
}
