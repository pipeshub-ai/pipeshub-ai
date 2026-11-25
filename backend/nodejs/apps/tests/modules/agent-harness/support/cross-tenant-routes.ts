/**
 * QA-26: the harness's tenant-facing HTTP surface as the app mounts it, and
 * the cross-tenant table that says, for every route, which caller may reach
 * which object. The inventory test (PR lane) holds the table equal to the
 * routers; the Mongo itest replays one org's and one owner's ids on every
 * row as the others.
 */
import { Router } from 'express';
import { bindingProof, ringBindingKey } from '../../../../src/modules/agent-harness/domain/sandbox-binding';
import { Types } from 'mongoose';
import { Container } from 'inversify';
import {
  createAgentHarnessSessionRouter,
  createChatHarnessSessionRouter,
} from '../../../../src/modules/agent-harness/transport/session-routes';
import {
  AGENT_HARNESS_ADMIN_PREFIX,
  createAgentHarnessAdminRouter,
} from '../../../../src/modules/agent-harness/transport/admin-routes';
import {
  AGENT_HARNESS_INTERNAL_PREFIX,
  createAgentHarnessInternalRouter,
} from '../../../../src/modules/agent-harness/transport/internal-routes';
import {
  MEMORIES_PREFIX,
  createMemoryRouter,
} from '../../../../src/modules/agent-harness/transport/memory-routes';
import {
  MODEL_ACCOUNTS_PREFIX,
  createModelAccountRouter,
} from '../../../../src/modules/agent-harness/transport/model-account-routes';
import {
  ENGINE_ACCOUNTS_PREFIX,
  createEngineSignInRouter,
} from '../../../../src/modules/agent-harness/transport/engine-signin-routes';
import {
  SCHEDULES_PREFIX,
  TRIGGERS_PREFIX,
  createScheduleRouter,
  createTriggerRouter,
} from '../../../../src/modules/agent-harness/transport/schedule-routes';
import {
  PUBLICATIONS_PREFIX,
  createPublicationRouter,
} from '../../../../src/modules/agent-harness/transport/publication-routes';
import {
  createAgentConversationalRouter,
  createConversationalRouter,
} from '../../../../src/modules/enterprise_search/routes/es.routes';
import {
  CAPABILITY_SYNC_PATH,
  createHarnessCapabilitySyncRouter,
} from '../../../../src/modules/agent-harness/transport/capability-sync-routes';
import {
  AGENT_CONFIG_PREFIX,
  createAgentConfigRouter,
} from '../../../../src/modules/agent-harness/transport/agent-config-routes';
import {
  AGENT_HARNESS_RUNS_PREFIX,
  createAgentHarnessRunsRouter,
} from '../../../../src/modules/agent-harness/transport/run-list-routes';
import { INBOX_PREFIX, createInboxRouter } from '../../../../src/modules/agent-harness/transport/inbox-routes';

/**
 * One router as `app.ts` mounts it. `conversationRoutesOnly`: the
 * conversation module's routers, of which the routes naming a conversation
 * reach harness state (the turn middleware, the active-run read, erasure,
 * shares and forks); the rest proxy the query service, which owns their
 * checks.
 */
export interface HarnessSurface {
  name: string;
  prefix: string;
  build: (container: Container) => Router;
  conversationRoutesOnly?: boolean;
}

export const HARNESS_SURFACES: readonly HarnessSurface[] = [
  { name: 'createChatHarnessSessionRouter', prefix: '/api/v1/conversations', build: createChatHarnessSessionRouter },
  { name: 'createConversationalRouter', prefix: '/api/v1/conversations', build: createConversationalRouter, conversationRoutesOnly: true },
  { name: 'createAgentConversationalRouter', prefix: '/api/v1/agents', build: createAgentConversationalRouter, conversationRoutesOnly: true },
  { name: 'createAgentHarnessSessionRouter', prefix: '/api/v1/agents', build: createAgentHarnessSessionRouter },
  { name: 'createAgentConfigRouter', prefix: AGENT_CONFIG_PREFIX, build: createAgentConfigRouter },
  { name: 'createAgentHarnessInternalRouter', prefix: AGENT_HARNESS_INTERNAL_PREFIX, build: createAgentHarnessInternalRouter },
  { name: 'createAgentHarnessAdminRouter', prefix: AGENT_HARNESS_ADMIN_PREFIX, build: createAgentHarnessAdminRouter },
  { name: 'createAgentHarnessRunsRouter', prefix: AGENT_HARNESS_RUNS_PREFIX, build: createAgentHarnessRunsRouter },
  { name: 'createModelAccountRouter', prefix: MODEL_ACCOUNTS_PREFIX, build: createModelAccountRouter },
  { name: 'createEngineSignInRouter', prefix: ENGINE_ACCOUNTS_PREFIX, build: createEngineSignInRouter },
  { name: 'createScheduleRouter', prefix: SCHEDULES_PREFIX, build: createScheduleRouter },
  { name: 'createPublicationRouter', prefix: PUBLICATIONS_PREFIX, build: createPublicationRouter },
  { name: 'createTriggerRouter', prefix: TRIGGERS_PREFIX, build: createTriggerRouter },
  { name: 'createMemoryRouter', prefix: MEMORIES_PREFIX, build: createMemoryRouter },
  { name: 'createHarnessCapabilitySyncRouter', prefix: CAPABILITY_SYNC_PATH, build: createHarnessCapabilitySyncRouter },
  { name: 'createInboxRouter', prefix: INBOX_PREFIX, build: createInboxRouter },
];

export interface MountedRoute {
  method: string;
  path: string;
}

export const routeKey = (route: MountedRoute): string => `${route.method} ${route.path}`;

interface RouteLayer {
  route?: { path: string; methods: Record<string, boolean> };
}

/** A route naming a conversation the caller reaches it by; `/internal/` ones take a service token instead. */
const namesConversation = (path: string): boolean =>
  path.includes(':conversationId') && !path.includes('/internal/');

/** Every route of the surfaces, as `METHOD /full/path`, in mount order. */
export function mountedRoutes(container: Container, surfaces: readonly HarnessSurface[] = HARNESS_SURFACES): MountedRoute[] {
  const routes: MountedRoute[] = [];
  for (const surface of surfaces) {
    const router = surface.build(container) as unknown as { stack: RouteLayer[] };
    for (const layer of router.stack) {
      if (layer.route === undefined) continue;
      const path = `${surface.prefix}${layer.route.path}`;
      if (surface.conversationRoutesOnly === true && !namesConversation(layer.route.path)) continue;
      for (const [method, on] of Object.entries(layer.route.methods)) {
        if (on && method !== '_all') routes.push({ method: method.toUpperCase(), path });
      }
    }
  }
  return routes;
}

/**
 * Who may reach the object a row names:
 * - `owner`: its owner (the conversation's, the run's actor, the memory's);
 * - `org`: any member of its org (an agent);
 * - `actor-or-admin`: its actor or an admin of its org (a schedule);
 * - `admin`: an admin of its org (an org account, a parked effect, an agent memory);
 * - `admin-reads-own-org`: an admin of the caller's own org, reading that org's records only; the query may name
 *   another org's ids, which then match nothing (the download audit, AUDITUI);
 * - `admin-own-org`: an admin of the caller's own org, on an object that org names for itself (an org cache by its
 *   kind's name: every org may have its own `pypi`); the path names no other org's object, so another org's admin
 *   acts on their own org's, and any member is refused;
 * - `caller`: the row names nothing but the caller's own (lists, settings, `me`);
 * - `relay`: another service decides with the caller's own credential (publications: the query service);
 * - `run-token`: the attempt or gateway token of the run in the path;
 * - `worker`: the deployment's worker credential, for the org the request names;
 * - `deployment`: the worker credential for whichever org the work is in: it names none (claim the next run) or any
 *   (an org's live layer, which a worker holds for every org it runs);
 * - `trigger`: the schedule's own secret;
 * - `link`: a single-use download link's own token, whoever presents it;
 * - `service`: a service token with the route's scope, for the org the token names (the body never names one).
 */
export type Guard =
  | 'owner'
  | 'org'
  | 'actor-or-admin'
  | 'admin'
  | 'admin-reads-own-org'
  | 'admin-own-org'
  | 'caller'
  | 'relay'
  | 'run-token'
  | 'worker'
  | 'deployment'
  | 'trigger'
  | 'link'
  | 'service';

/** One tenant's objects, every one of them reachable by its rightful caller. */
export interface TenantIds {
  orgId: string;
  ownerId: string;
  agentKey: string;
  agentSessionId: string;
  agentRunId: string;
  agentMessageId: string;
  chatSessionId: string;
  chatRunId: string;
  chatMessageId: string;
  interactionId: string;
  commandId: string;
  /** A tool call of the agent run (ACTRESULTS: its step's result). */
  toolCallId: string;
  workspaceId: string;
  revision: string;
  reservationKey: string;
  scheduleId: string;
  memoryId: string;
  agentMemoryId: string;
  publicationId: string;
  unknownRunId: string;
  unknownInteractionId: string;
}

export interface Caller {
  orgId: string;
  userId: string;
}

export interface CrossTenantRow {
  guard: Guard;
  /** A valid body, so a refusal is the route's decision and never its validation's. */
  body?: (target: TenantIds, caller: Caller) => unknown;
  /** The query string; `{name}` is the target tenant's id of that name (`{org}` its org). */
  query?: string;
  headers?: (target: TenantIds) => Record<string, string>;
  /** Answers with SSE: read until the first frame or the end. */
  stream?: boolean;
  /** A read its rightful caller gets 2xx on with the same ids (the sweep's positive control). */
  live?: boolean;
  /** An admin of the object's org may also reach it (an approver's view of a held call). */
  adminMay?: boolean;
  /**
   * How the route says no besides 403 and 404, by its published contract:
   * `idempotent-200` (a delete that matched nothing, `conversation: null`);
   * `in-band-sse` (a stream that opens and only errors: nothing regenerated).
   */
  refusal?: 'idempotent-200' | 'in-band-sse';
  /** A `worker` row whose route takes another machine role's credential, not the worker's (SEC-C-10). */
  role?: 'egress-proxy';
}

const key = (suffix: string) => ({ idempotencyKey: `qa26-${suffix}` });
const turn = (agent: boolean) => ({ query: 'qa26 replayed turn', chatMode: agent ? 'quick' : 'internal_search', ...key('turn') });
const esQuery = (agent: boolean) => ({ query: 'qa26 replayed turn', chatMode: agent ? 'quick' : 'internal_search' });

const SESSION_ROWS = (base: string, agent: boolean): Record<string, CrossTenantRow> => ({
  [`POST ${base}/runs`]: { guard: 'owner', body: () => turn(agent) },
  [`GET ${base}/runs/:runId`]: { guard: 'owner', live: true },
  // No positive control: an unpinned run (CONTROL_PLANE_PIN off) answers its owner 404 too (CPD-5).
  [`GET ${base}/runs/:runId/resolved`]: { guard: 'owner' },
  [`GET ${base}/runs/:runId/stream`]: { guard: 'owner', stream: true, live: true },
  [`POST ${base}/runs/:runId/cancel`]: { guard: 'owner', body: () => key('cancel') },
  [`POST ${base}/runs/:runId/resume`]: { guard: 'owner', body: () => key('resume') },
  [`GET ${base}/runs/:runId/timeline`]: { guard: 'owner', live: true },
  [`GET ${base}/runs/:runId/cost`]: { guard: 'owner', live: true },
  [`GET ${base}/runs/:runId/activity`]: { guard: 'owner', stream: true, live: true },
  [`GET ${base}/runs/:runId/commands/:commandId/log`]: { guard: 'owner', live: true },
  // ACTRESULTS: an opened step's result. The seeded chat run made no tool call, so only the agent route has a positive control.
  [`GET ${base}/runs/:runId/steps/:toolCallId`]: agent ? { guard: 'owner', live: true } : { guard: 'owner' },
  [`GET ${base}/runs/:runId/followups`]: { guard: 'owner', live: true },
  [`GET ${base}/usage`]: { guard: 'owner', live: true },
  [`POST ${base}/compact`]: { guard: 'owner', body: () => ({ focus: 'qa26' }) },
  [`GET ${base}/runs/:runId/interactions/authority`]: { guard: 'owner', live: true, adminMay: true },
  [`GET ${base}/workspace/versions`]: { guard: 'owner', live: true },
  [`GET ${base}/workspace/diff`]: { guard: 'owner', live: true },
  // SLASH3: the sandbox commands, the owner's alone.
  [`GET ${base}/workspace/files`]: { guard: 'owner', live: true },
  [`POST ${base}/workspace/files`]: { guard: 'owner' },
  [`GET ${base}/workspace/file`]: { guard: 'owner', query: 'path=qa26.txt' },
  [`GET ${base}/workspace/archive`]: { guard: 'owner' },
  [`POST ${base}/commands/run`]: { guard: 'owner', body: () => ({ command: 'ls', chatMode: agent ? 'quick' : 'internal_search', ...key('run') }) },
  [`PUT ${base}/coding-engine`]: { guard: 'owner', body: () => ({ engine: 'none' }) },
  // GIT-FLOW (WP-26): the chat's GitHub repository, the owner's alone; the rig seeds one on each chat.
  [`GET ${base}/repository`]: { guard: 'owner', live: true },
  [`PUT ${base}/repository`]: {
    guard: 'owner',
    body: () => ({ url: 'https://github.com/qa26/replayed', toolsetInstanceId: 'qa26-replayed' }),
  },
  [`DELETE ${base}/repository`]: { guard: 'owner' },
  [`GET ${base}/sandbox`]: { guard: 'owner', live: true },
  [`DELETE ${base}/sandbox/kept`]: { guard: 'owner' },
  [`POST ${base}/workspace/versions/:revision/restore`]: { guard: 'owner', body: () => ({ baseRevision: 1, ...key('restore') }) },
  [`POST ${base}/runs/:runId/interactions/:interactionId/decision`]: { guard: 'owner', body: () => ({ decision: 'approved', ...key('decide') }) },
  [`POST ${base}/runs/:runId/interactions/decisions`]: {
    guard: 'owner',
    body: (target) => ({ interactionIds: [target.interactionId], decision: 'approved', ...key('decide-all') }),
  },
  // UI-B1 (ADR-UI-01 D3): a job's brief, the owner's alone. The seeded runs are root runs (no job), so no positive control.
  [`GET ${base}/runs/:runId/brief`]: { guard: 'owner' },
  // UI-B5 (ADR-UI-01 D5, D6): how a job's goal changed (a job only, as the brief), and one request's jobs (any run of
  // the conversation, so the seeded root run is the positive control). The owner's alone.
  [`GET ${base}/runs/:runId/brief/history`]: { guard: 'owner' },
  [`GET ${base}/runs/:runId/jobs`]: { guard: 'owner', live: true },
  // ADR-UI-01 D2: the caller's own seen mark, on a conversation they may open.
  [`PUT ${base}/seen`]: { guard: 'owner', body: () => ({}) },
  // ADR-GRANT-01 D8: the conversation's grants, listed and revoked by its owner only.
  [`GET ${base}/grants`]: { guard: 'owner', live: true },
  [`DELETE ${base}/grants/:grantId`]: { guard: 'owner' },
});

const CONVERSATION_ROWS = (base: string, agent: boolean): Record<string, CrossTenantRow> => ({
  [`POST ${base}/messages`]: { guard: 'owner', body: () => esQuery(agent) },
  [`POST ${base}/messages/stream`]: { guard: 'owner', body: () => esQuery(agent) },
  [`GET ${base}`]: { guard: 'owner', live: true },
  [`DELETE ${base}`]: { guard: 'owner', ...(agent ? { refusal: 'idempotent-200' as const } : {}) },
  [`POST ${base}/fork`]: { guard: 'owner', body: () => key('fork') },
  [`PATCH ${base}/title`]: { guard: 'owner', body: () => ({ title: 'qa26 renamed' }) },
  [`PUT ${base}/project`]: { guard: 'owner', body: () => ({ projectId: null }) },
  [`PATCH ${base}/project-visibility`]: { guard: 'owner', body: () => ({ visibility: 'private' }) },
  [`POST ${base}/message/:messageId/regenerate`]: { guard: 'owner', body: () => ({ chatMode: agent ? 'quick' : 'internal_search' }), refusal: 'in-band-sse' },
  [`POST ${base}/cancel`]: { guard: 'owner', body: (target) => ({ runId: target[agent ? 'agentRunId' : 'chatRunId'] }) },
  [`POST ${base}/message/:messageId/feedback`]: { guard: 'owner', body: () => ({ isHelpful: false }) },
  // Main's fork from a message (#4225): the owner only; anyone else gets 404.
  [`POST ${base}/message/:messageId/fork`]: { guard: 'owner' },
  [`${agent ? 'POST' : 'PATCH'} ${base}/archive`]: { guard: 'owner' },
  [`${agent ? 'POST' : 'PATCH'} ${base}/unarchive`]: { guard: 'owner' },
  // Main #4453 shares an agent chat too, read-only: the owner only, as a plain chat's. An agent chat refuses
  // sharing with oneself before it looks the chat up, so the replay names someone who is neither tenant's.
  [`POST ${base}/share`]: {
    guard: 'owner',
    body: (_target: TenantIds, caller: Caller) => ({ userIds: [agent ? new Types.ObjectId().toHexString() : caller.userId] }),
  },
  [`POST ${base}/unshare`]: { guard: 'owner', body: (_target: TenantIds, caller: Caller) => ({ userIds: [caller.userId] }) },
  ...(agent
    ? {}
    : {
        // Main's pinned chats (#4363): the owner's own sidebar.
        [`PATCH ${base}/pin`]: { guard: 'owner' },
        [`PATCH ${base}/unpin`]: { guard: 'owner' },
        // Main's model compare (#5358, behind ENABLE_CHAT_COMPARE): the conversation's owner only.
        [`POST ${base}/compare/branch`]: { guard: 'owner', body: () => ({ userMessageId: new Types.ObjectId().toHexString() }) },
        [`GET ${base}/compare`]: { guard: 'owner' },
        [`POST ${base}/compare/choose`]: { guard: 'owner', body: () => ({ choice: 'primary' }) },
      }),
});

const INTERNAL = AGENT_HARNESS_INTERNAL_PREFIX;
const WORKSPACE = `${INTERNAL}/runs/:runId/workspaces/:workspaceId`;
const attempt = (body?: CrossTenantRow['body']): CrossTenantRow => ({ guard: 'run-token', ...(body ? { body } : {}) });
const fenced = () => ({ attemptId: 'qa26-attempt', fence: 1 });

/** Every tenant-facing harness route and who may reach what it names. */
export const CROSS_TENANT_ROUTES: Readonly<Record<string, CrossTenantRow>> = {
  'POST /api/v1/conversations/runs': { guard: 'caller' },
  ...SESSION_ROWS('/api/v1/conversations/:sessionId', false),
  ...CONVERSATION_ROWS('/api/v1/conversations/:conversationId', false),
  ...CONVERSATION_ROWS('/api/v1/agents/:agentKey/conversations/:conversationId', true),
  'POST /api/v1/agents/:agentKey/runs': { guard: 'org', body: () => turn(true) },
  ...SESSION_ROWS('/api/v1/agents/:agentKey/conversations/:sessionId', true),

  [`GET ${INTERNAL}/runs/:runId/audit`]: { guard: 'worker', query: 'orgId={org}' },
  [`POST ${INTERNAL}/runs/claim`]: { guard: 'deployment' },
  [`GET ${INTERNAL}/control-plane/orgs/:orgId/live`]: { guard: 'deployment' },
  // C9.04: the internal-only services' metrics; names no org, and no series it answers carries one (R11-MX-9).
  [`GET ${INTERNAL}/metrics`]: { guard: 'deployment' },
  // ADR-OPS-01 D4.3: the egress proxy's stops; names no org beyond null (every org).
  // CPD-7's dual write: the query service's capabilities:sync token, its org in the token.
  [`POST ${CAPABILITY_SYNC_PATH}/`]: { guard: 'service' },
  [`POST ${INTERNAL}/runs/:runId/claim`]: { guard: 'worker', body: () => ({ workerId: 'qa26-w', leaseDurationMs: 60_000 }) },
  [`POST ${INTERNAL}/runs/:runId/renew`]: attempt(() => ({ ...fenced(), leaseDurationMs: 60_000 })),
  [`POST ${INTERNAL}/runs/:runId/release`]: attempt(fenced),
  [`POST ${INTERNAL}/runs/:runId/commit`]: attempt(() => ({ ...fenced(), commandId: 'qa26-commit', events: [] })),
  [`GET ${INTERNAL}/runs/:runId/events`]: attempt(),
  [`POST ${INTERNAL}/runs/:runId/followups`]: attempt(() => ({ ...fenced(), items: [{ text: 'qa26?' }] })),
  [`POST ${INTERNAL}/runs/:runId/children`]: attempt(() => ({ ...fenced() })),
  [`GET ${INTERNAL}/runs/:runId/budget`]: attempt(),
  [`POST ${INTERNAL}/runs/:runId/budget/reservations`]: attempt(() => ({ key: 'qa26-r', requested: { usdMicros: 1, tokens: 1 }, minimum: { usdMicros: 1, tokens: 1 }, ttlS: 60 })),
  [`POST ${INTERNAL}/runs/:runId/budget/reservations/:key/commit`]: attempt(() => ({ actual: { usdMicros: 1, tokens: 1 } })),
  [`POST ${INTERNAL}/runs/:runId/budget/reservations/:key/release`]: attempt(() => ({})),
  [`POST ${INTERNAL}/runs/:runId/budget/reservations/:key/reconcile`]: attempt(() => ({ actual: { usdMicros: 1, tokens: 1 } })),
  [`POST ${INTERNAL}/runs/:runId/content`]: attempt(),
  [`GET ${INTERNAL}/runs/:runId/content`]: attempt(),
  [`GET ${WORKSPACE}`]: attempt(),
  [`GET ${WORKSPACE}/revisions/:revision`]: attempt(),
  [`POST ${WORKSPACE}/commit`]: attempt(fenced),
  [`POST ${WORKSPACE}/fork`]: attempt(fenced),
  [`POST ${WORKSPACE}/merge`]: attempt(fenced),
  [`POST ${WORKSPACE}/rollback`]: attempt(fenced),
  [`POST ${WORKSPACE}/writer`]: attempt(fenced),
  [`POST ${WORKSPACE}/writer/release`]: attempt(fenced),
  [`POST ${INTERNAL}/runs/:runId/workspace-key`]: attempt(fenced),
  [`GET ${INTERNAL}/runs/:runId/memories`]: attempt(),
  [`POST ${INTERNAL}/runs/:runId/memories`]: attempt(fenced),
  [`POST ${INTERNAL}/runs/:runId/gateway-token`]: {
    guard: 'worker',
    role: 'egress-proxy',
    // A binding the broker signed for the caller's org and this run, so the refusal is the run's, not the proof's.
    body: (target, caller) => ({
      attemptId: 'qa26-attempt',
      bindingProof: bindingProof(ringBindingKey(process.env.SECRET_KEY ?? ''), {
        orgId: caller.orgId,
        runId: target.agentRunId,
        attemptId: 'qa26-attempt',
      }),
    }),
  },
  [`GET ${INTERNAL}/runs/:runId/model-grant`]: attempt(),
  [`POST ${INTERNAL}/runs/:runId/refusals`]: attempt(() => ({ gateway: 'model', reason: 'qa26', status: 403 })),
  [`POST ${INTERNAL}/runs/:runId/egress`]: attempt(() => ({ host: 'qa26.example', via: 'http' })),
  [`POST ${INTERNAL}/runs/:runId/egress-volume`]: attempt(() => ({ kind: 'run_threshold', direction: 'up', bytes: 2, thresholdBytes: 1, alias: 'tunnel' })),
  [`POST ${INTERNAL}/runs/:runId/model-fallback`]: attempt(() => ({ from: { provider: 'openai', model: 'qa26-a' }, to: { provider: 'openai', model: 'qa26-b' }, reason: '5xx' })),
  [`POST ${INTERNAL}/runs/:runId/secret-hidden`]: attempt(() => ({ gateway: 'egress', kind: 'pipeshub_pat' })),

  // SIGNIN: the caller's own sign-in (the path never names a user); an attempt answers its owner only, through the
  // worker holding it, and is not found for anyone else.
  [`GET ${ENGINE_ACCOUNTS_PREFIX}/me`]: { guard: 'caller' },
  [`PUT ${ENGINE_ACCOUNTS_PREFIX}/me/claude`]: { guard: 'caller', body: () => ({ enabled: false }) },
  [`DELETE ${ENGINE_ACCOUNTS_PREFIX}/me/claude`]: { guard: 'caller' },
  [`GET ${ENGINE_ACCOUNTS_PREFIX}/signin/:attemptId`]: { guard: 'relay' },
  [`DELETE ${ENGINE_ACCOUNTS_PREFIX}/signin/:attemptId`]: { guard: 'relay' },
  [`POST ${ENGINE_ACCOUNTS_PREFIX}/signin/:attemptId/code`]: { guard: 'relay', body: () => ({ code: 'qa26#state' }) },
  [`PUT ${AGENT_HARNESS_ADMIN_PREFIX}/coding-engine`]: { guard: 'caller' },
  // ADR-UI-01 D1 (UI-B1 G1, G7): the caller's own runs and jobs; another person's or org's never appear.
  [`GET ${AGENT_HARNESS_RUNS_PREFIX}/`]: { guard: 'caller', live: true },
  // CH-07: names no tenant's object, only which store this server found degraded (no error text).
  [`GET ${AGENT_HARNESS_ADMIN_PREFIX}/dependencies`]: { guard: 'caller' },
  [`GET ${MODEL_ACCOUNTS_PREFIX}/access`]: { guard: 'caller' },
  [`GET ${MODEL_ACCOUNTS_PREFIX}/coding-engine`]: { guard: 'caller' },
  [`PUT ${MODEL_ACCOUNTS_PREFIX}/coding-engine`]: { guard: 'caller' },
  [`POST ${SCHEDULES_PREFIX}/`]: { guard: 'caller' },
  // UI-3: the notice channels a schedule may use; naming an agent answers for that agent's Slack bot, in the agent's org only.
  [`GET ${SCHEDULES_PREFIX}/notify-channels`]: { guard: 'org', query: 'agentKey={agentKey}', live: true },
  [`GET ${SCHEDULES_PREFIX}/`]: { guard: 'caller', query: 'scope=mine' },
  [`GET ${SCHEDULES_PREFIX}/:scheduleId`]: { guard: 'actor-or-admin', live: true },
  [`GET ${SCHEDULES_PREFIX}/:scheduleId/firings`]: { guard: 'actor-or-admin', live: true },
  [`DELETE ${SCHEDULES_PREFIX}/:scheduleId`]: { guard: 'actor-or-admin' },
  [`POST ${SCHEDULES_PREFIX}/:scheduleId/pause`]: { guard: 'actor-or-admin' },
  [`POST ${SCHEDULES_PREFIX}/:scheduleId/resume`]: { guard: 'actor-or-admin' },
  [`PUT ${SCHEDULES_PREFIX}/:scheduleId/agent-version`]: { guard: 'owner', body: () => ({ agentVersion: 'latest' }) },
  [`POST ${SCHEDULES_PREFIX}/:scheduleId/run`]: { guard: 'owner', headers: () => ({ 'idempotency-key': 'qa26-run-now' }) },
  [`POST ${PUBLICATIONS_PREFIX}/`]: {
    guard: 'relay',
    body: () => ({ containerKind: 'kb', containerId: 'kb-qa26', audience: { userIds: [], teamIds: [], org: true }, access: 'full' }),
  },
  [`GET ${PUBLICATIONS_PREFIX}/`]: { guard: 'relay', query: 'containerKind=kb&containerId=kb-qa26' },
  [`DELETE ${PUBLICATIONS_PREFIX}/:publicationId`]: { guard: 'relay' },
  // C4.08: the query service decides who may read the agent, with the caller's own credential; only then is its
  // policy layer read, in the caller's org. An import names nothing but the caller's own (a new agent of theirs).
  [`GET ${AGENT_CONFIG_PREFIX}/:agentKey/export`]: { guard: 'relay' },
  [`POST ${AGENT_CONFIG_PREFIX}/import`]: { guard: 'caller' },
  [`POST ${TRIGGERS_PREFIX}/:scheduleId/webhook`]: { guard: 'trigger', headers: () => ({ 'x-pipeshub-trigger-token': 'qa26-not-the-secret' }) },
  [`GET ${MEMORIES_PREFIX}/`]: { guard: 'caller' },
  // G2 (ADR-INBOX-01): one Inbox per person. The feed, counts and settings are the caller's own; a notice action
  // names the caller's notices by id, and anyone else's id matches nothing (inbox-feed.test.ts IBX-F7). A mute names
  // a schedule the caller acts as, or administers.
  [`GET ${INBOX_PREFIX}/`]: { guard: 'caller' },
  [`GET ${INBOX_PREFIX}/count`]: { guard: 'caller' },
  [`PATCH ${INBOX_PREFIX}/notices`]: { guard: 'caller', body: () => ({ action: 'read', all: true }) },
  // UIP3-FU-2: the caller's own archived notices; another org's ids match nothing of theirs.
  [`POST ${INBOX_PREFIX}/notices/delete`]: { guard: 'caller', body: () => ({ all: true }) },
  [`GET ${INBOX_PREFIX}/preferences`]: { guard: 'caller' },
  [`PATCH ${INBOX_PREFIX}/preferences`]: {
    guard: 'actor-or-admin',
    body: (target) => ({ mute: { scheduleId: target.scheduleId, muted: true } }),
  },
  // MEMSW: the caller's own memory switch; the path names no one else's.
  [`GET ${MEMORIES_PREFIX}/preference`]: { guard: 'caller' },
  [`PUT ${MEMORIES_PREFIX}/preference`]: { guard: 'caller', body: () => ({ remember: false }) },
  [`PATCH ${MEMORIES_PREFIX}/:memoryId`]: { guard: 'owner', body: () => ({ content: 'qa26 overwritten memory' }) },
  [`DELETE ${MEMORIES_PREFIX}/:memoryId`]: { guard: 'owner' },
  [`POST ${MEMORIES_PREFIX}/:memoryId/review`]: { guard: 'owner', body: () => ({ decision: 'accepted' }) },
};

/** A row's query string for a tenant: each `{name}` is that tenant's id of the name, `{org}` its org. */
export const rowQuery = (row: CrossTenantRow, ids: TenantIds): string =>
  row.query === undefined
    ? ''
    : `?${row.query.replace(/\{([A-Za-z]+)\}/g, (_match, name: string) =>
        encodeURIComponent(name === 'org' ? ids.orgId : String((ids as unknown as Record<string, string>)[name])),
      )}`;

/**
 * The params that name a tenant's object. `provider` and a cache's `name` (WP-100) name the caller's own: every
 * org has its own namespace of them, so the same name in another org's request is that org's, never this one's.
 */
export const OWN_NAMESPACE_PARAMS: ReadonlySet<string> = new Set(['provider', 'name']);
const TENANT_PARAMS = new Set([
  'agentKey',
  'sessionId',
  'conversationId',
  'runId',
  'messageId',
  'interactionId',
  'commandId',
  'toolCallId',
  'workspaceId',
  'revision',
  'key',
  'scheduleId',
  'memoryId',
  'publicationId',
  // SIGNIN: a member an admin signs out, and a sign-in attempt (named after its run's attempt).
  'userId',
  'attemptId',
  // ADR-OPS-01: a control of the org (an agent's stop), named under its org.
  'controlId',
]);

export const pathParams = (path: string): string[] =>
  [...path.matchAll(/:([A-Za-z]+)/g)].map((match) => match[1]!);

export const tenantParams = (path: string): string[] => pathParams(path).filter((name) => TENANT_PARAMS.has(name));

/** The tenant objects a row's query names (`{agentKey}`); `{org}` is the caller's own org, not an object. */
export const tenantQueryParams = (row: CrossTenantRow): string[] =>
  [...(row.query ?? '').matchAll(/\{([A-Za-z]+)\}/g)].map((match) => match[1]!).filter((name) => TENANT_PARAMS.has(name));

const isAgentPath = (path: string): boolean => path.startsWith('/api/v1/agents/');

/** The object of `ids` a path parameter names on this route. */
export function paramValue(name: string, path: string, ids: TenantIds): string {
  const agent = isAgentPath(path) || path.startsWith(INTERNAL);
  const unknown = path.includes('/unknown-effects/');
  switch (name) {
    case 'sessionId':
    case 'conversationId':
      return agent ? ids.agentSessionId : ids.chatSessionId;
    case 'runId':
      return unknown ? ids.unknownRunId : agent ? ids.agentRunId : ids.chatRunId;
    case 'messageId':
      return agent ? ids.agentMessageId : ids.chatMessageId;
    case 'interactionId':
      return unknown ? ids.unknownInteractionId : ids.interactionId;
    case 'memoryId':
      return path.startsWith(AGENT_HARNESS_ADMIN_PREFIX) ? ids.agentMemoryId : ids.memoryId;
    case 'key':
      return ids.reservationKey;
    case 'provider':
      return 'openAI';
    case 'attemptId':
      // SIGNIN: a sign-in attempt is named after the run's attempt; the worker holding it answers its owner only.
      return `sin_${ids.agentRunId}.a1`;
    case 'userId':
      return ids.ownerId;
    case 'controlId':
      return `org:${ids.orgId}:agent:${ids.agentKey}`;
    case 'name':
      return 'pypi';
    case 'digest':
      return `sha256:${'a'.repeat(64)}`;
    case 'grantId':
      // ADR-GRANT-01 D8: a grant is named within its conversation (its tool and destination, digested).
      return `cg_${'0'.repeat(40)}`;
    default: {
      const value = (ids as unknown as Record<string, string>)[name];
      if (value === undefined) throw new Error(`no tenant id for :${name} on ${path}`);
      return value;
    }
  }
}

/** The concrete path, each tenant parameter taken from the tenant `pick` names for it. */
export function concretePath(path: string, pick: (name: string) => TenantIds): string {
  return path.replace(/:([A-Za-z]+)/g, (_match, name: string) => encodeURIComponent(paramValue(name, path, pick(name))));
}
