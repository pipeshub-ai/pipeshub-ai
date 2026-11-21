/**
 * The existing chat routes over real HTTP, for the chat-turn suites (P1b C-5,
 * LAT1): `chatTurnMiddleware` in front of a stand-in legacy handler, with a
 * real RunAdmissionService on the in-memory store, the real rollout policy,
 * SessionRunService and RunStreamService. The run's journal is a golden
 * durable run, as the worker would have written it for the admitted run.
 */
import type { RunInputSeal } from '../../../../src/modules/agent-harness/application/run-input-seal';
import 'reflect-metadata';
import express from 'express';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import sinon from 'sinon';
import { Types } from 'mongoose';
import { Logger } from '../../../../src/libs/services/logger.service';
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware';
import { InMemoryExecutionStore } from '../../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { InMemoryJournalTail } from '../../../../src/modules/agent-harness/adapters/in-memory-journal-tail';
import { HarnessLogger } from '../../../../src/modules/agent-harness/application/harness-log';
import { HarnessRollout } from '../../../../src/modules/agent-harness/application/harness-rollout';
import { JournalService } from '../../../../src/modules/agent-harness/application/journal-service';
import { RunAdmissionService } from '../../../../src/modules/agent-harness/application/run-admission-service';
import { SessionRunService } from '../../../../src/modules/agent-harness/application/session-run-service';
import { ChatTurnService } from '../../../../src/modules/agent-harness/application/chat-turns';
import { ExecutionService, JournalReader } from '../../../../src/modules/agent-harness/application/execution-service';
import { HarnessRun } from '../../../../src/modules/agent-harness/domain/execution';
import { HarnessRolloutSettings } from '../../../../src/modules/agent-harness/domain/rollout';
import { RunStreamService } from '../../../../src/modules/agent-harness/projection/stream-service';
import {
  ChatSessionRepository,
  SessionPlacement,
} from '../../../../src/modules/agent-harness/ports/chat-session-repository';
import { RoutingDecider } from '../../../../src/modules/agent-harness/ports/routing-decider';
import type { SessionRoleLookup } from '../../../../src/modules/agent-harness/ports/session-roles';
import {
  chatTurnMiddleware,
  type ChatTurnRouteOptions,
  mcpSurface,
  slackSurface,
  webSurface,
} from '../../../../src/modules/agent-harness/transport/chat-turn-route';
import { readJournal } from '../../../contracts/support/durable';
import { grantingAccess } from './agent-definition';
import { asParty } from './store-race';
import { chatCancelMiddleware } from '../../../../src/modules/agent-harness/transport/chat-cancel-route';
import { DURABLE_CAPABILITIES } from './execution-store-contract';
import { RunAdmissionStore } from '../../../../src/modules/agent-harness/domain/admission';
import { ExecutionStore } from '../../../../src/modules/agent-harness/ports/execution-store';
import { testCipher, testRunKeys } from './data-protection';

export type Line = [level: string, message: string, meta: Record<string, unknown>];

export const ORG = new Types.ObjectId().toHexString();
export const USER = new Types.ObjectId().toHexString();
export const SLACK_EMAIL = 'sam@slack.example';
/** WP62-RUNID: a second member of ORG, and a member of another org, to show a client run id is only ever its sender's. */
export const OTHER_USER = new Types.ObjectId().toHexString();
export const OTHER_ORG = new Types.ObjectId().toHexString();
export const OTHER_ORG_USER = new Types.ObjectId().toHexString();

/** Identities as the auth middleware leaves them; `service` is the Slack bot's scoped token. */
const identities: Record<string, { user?: Record<string, unknown>; tokenPayload?: Record<string, unknown> }> = {
  pat: { user: { orgId: ORG, userId: USER, isOAuth: true, oauthClientId: `pat-system:${ORG}`, oauthScopes: ['conversation:chat'] } },
  oauth: { user: { orgId: ORG, userId: USER, isOAuth: true, oauthClientId: 'client-app-1', oauthScopes: ['conversation:chat'] } },
  // A PAT its owner limited to one collection (main #4451).
  patLimited: {
    user: {
      orgId: ORG, userId: USER, isOAuth: true, oauthClientId: `pat-system:${ORG}`, oauthScopes: ['conversation:chat'],
      knowledgeLimit: { apps: [], recordGroups: ['kb-1'], records: [] },
    },
  },
  session: { user: { orgId: ORG, userId: USER } },
  otherUser: { user: { orgId: ORG, userId: OTHER_USER } },
  otherOrg: { user: { orgId: OTHER_ORG, userId: OTHER_ORG_USER } },
  service: { tokenPayload: { email: SLACK_EMAIL, scopes: ['conversation:create'] } },
};

export interface ChatTurnHarness {
  post(path: string, identity: string, body: Record<string, unknown>, headers?: Record<string, string>): Promise<Response>;
  lines: Line[];
  store: InMemoryExecutionStore;
  executions: ExecutionService;
  legacy: sinon.SinonSpy;
  identify: sinon.SinonSpy;
  submit: sinon.SinonSpy;
  close(): Promise<void>;
}

export interface ChatTurnHarnessOptions {
  enabled?: boolean;
  settings?: Partial<HarnessRolloutSettings>;
  conversation?: Record<string, unknown>;
  scenario?: string;
  identifyFails?: boolean;
  /** Who the public routes serve: only MCP-marked requests (P1b), or every turn (LAT1). */
  publicSurface?: 'mcp' | 'web';
  /** The conversation's record as routing reads it. */
  placement?: SessionPlacement | null;
  /** How the caller watches the session. */
  audience?: 'owner' | 'shared';
  /** Admission's own failure, after routing. */
  admitFails?: Error;
  /** The turn's router; absent: today's rollout from `settings` and `placement`. */
  routing?: RoutingDecider;
  /**
   * Admission and run reads on these stores instead of the in-memory one (QA-21, LAT2 on Mongo); with
   * `journal`, `runs` is a whole execution store and controls (a cancel) reach it.
   */
  stores?: { admission: RunAdmissionStore; runs: Pick<ExecutionStore, 'get'>; journal?: JournalReader; inputs?: RunInputSeal };
  /** The caller is no participant of the session (stream access refused). */
  noSessionAccess?: boolean;
  /** The routes' command expansion, in front of the chat follow-up and regenerate routes (SLASH1, LC2FIX). */
  commands?: { send: express.RequestHandler; regenerate: express.RequestHandler };
  /** G-41: the org's model groups on the turns today's loop serves. */
  modelGroups?: ChatTurnRouteOptions['modelGroups'];
  /** The participants' roles admission reads (SS0, SEC-C-12); absent: none bound. */
  roles?: SessionRoleLookup;
}

/** A request with this header runs as that store-race party (support/store-race.ts). */
export const PARTY_HEADER = 'x-test-party';

export const logged = (lines: Line[], event: string): Array<Record<string, unknown>> =>
  lines.filter(([, message]) => message === event).map(([, , meta]) => meta);

/** The SSE frames of a response body, with their ids. */
export const sseFrames = (body: string): Array<{ id?: string; data: Record<string, unknown> }> =>
  body
    .split('\n\n')
    .filter((block) => block.includes('data: '))
    .map((block) => {
      const id = block.split('\n').find((line) => line.startsWith('id: '));
      const data = block.split('\n').find((line) => line.startsWith('data: ')) as string;
      return {
        ...(id !== undefined ? { id: id.slice(4) } : {}),
        data: JSON.parse(data.slice(6)) as Record<string, unknown>,
      };
    });

export async function startChatTurnHarness(options: ChatTurnHarnessOptions = {}): Promise<ChatTurnHarness> {
  const lines: Line[] = [];
  const at = (level: string) => (message: string, meta: Record<string, unknown> = {}) => {
    lines.push([level, message, meta]);
  };
  const logger: HarnessLogger = { debug: at('debug'), info: at('info'), warn: at('warn'), error: at('error') };
  // The Mongo stores (harness-mongo) key runs with testRunKeys() too, so cancel reads the same digest either way.
  const runKeys = testRunKeys();
  const store = new InMemoryExecutionStore(undefined, undefined, undefined, null, undefined, null, runKeys);
  if (options.admitFails !== undefined) sinon.stub(store, 'admit').rejects(options.admitFails);
  const settings: HarnessRolloutSettings = {
    infrastructureReady: true,
    killSwitch: false,
    orgEnabled: true,
    modelFamilies: null,
    ...options.settings,
  };
  const rollout = new HarnessRollout({ read: async () => settings }, { resolve: async () => null });
  const runStore = options.stores?.runs ?? store;
  const admissions = new RunAdmissionService({
    store: options.stores?.admission ?? store,
    rollout,
    agentAccess: grantingAccess(),
    logger,
    ...(options.placement !== undefined ? { sessions: { placement: async () => options.placement ?? null } } : {}),
    ...(options.routing !== undefined ? { routing: options.routing } : {}),
    ...(options.roles !== undefined ? { roles: options.roles } : {}),
  });
  // A conversation keeps the title its admission stored it with, as the real repository reads it back.
  const titles = new Map<string, string>();
  const admit = admissions.submit.bind(admissions);
  admissions.submit = async (request) => {
    const outcome = await admit(request);
    if (outcome.route === 'durable' && outcome.admission.createdSession !== undefined) {
      titles.set(outcome.admission.sessionId, outcome.admission.createdSession.title);
    }
    return outcome;
  };
  const submit = sinon.spy(admissions, 'submit');
  const sessions = {
    findStreamAccess: async (scope: { sessionId: string }) =>
      options.noSessionAccess === true
        ? null
        : { audience: options.audience ?? 'owner', title: titles.get(scope.sessionId) ?? 'Surface turn' },
  } as unknown as ChatSessionRepository;
  const payloads = new InMemoryPayloadStore();
  // Callers that pass only `get` read runs; only the in-memory store or a full store with its journal is controlled.
  const executions = new ExecutionService({
    store: runStore as ExecutionStore,
    inputs: options.stores?.inputs ?? null,
    journal: options.stores?.journal ?? store.journal,
    journals: new JournalService(payloads, testCipher()),
    capabilities: DURABLE_CAPABILITIES,
  });
  const runs = new SessionRunService(executions, sessions);
  const golden = readJournal(options.scenario ?? 'chat_answer_with_citations');
  // What the worker journaled for the admitted run.
  const reader = {
    readRun: async (org: string, runId: string, after: number, limit: number) => {
      const run = (await runStore.get(org, runId)) as HarnessRun;
      return golden.events
        .map((e) => ({ ...e, orgId: org, runId, sessionId: run.sessionId, streamId: run.sessionId }))
        .filter((e) => e.seq > after)
        .slice(0, limit);
    },
  };
  const streams = new RunStreamService({
    journal: reader,
    journals: new JournalService(payloads, testCipher()),
    tail: new InMemoryJournalTail(),
    conversations: { finishedConversation: async () => options.conversation ?? { _id: 'c', messages: [] } },
    liveBus: null,
    logger,
  });
  const service = new ChatTurnService({ admissions, runs, streams, logger, runKeys });
  // Today's cancel answers `{cancelled: false}` for a run it does not know.
  const legacy = sinon.spy((req: express.Request, res: express.Response) => {
    if (req.path.endsWith('/cancel')) res.status(200).json({ cancelled: false, legacy: true });
    else res.status(200).type('text/plain').send('legacy');
  });
  const identify = sinon.spy(async (req: express.Request) => {
    if (options.identifyFails === true) throw new Error('User not found');
    // The Slack hydration: a service account with no PipesHub account.
    (req as express.Request & { user?: unknown }).user = {
      userId: new Types.ObjectId(USER),
      orgId: new Types.ObjectId(ORG),
      email: SLACK_EMAIL,
      isServiceAccount: true,
    };
  });
  const enabled = () => options.enabled ?? true;
  const publicSurface = options.publicSurface === 'web' ? webSurface : mcpSurface;
  const app = express();
  app.use(express.json());
  app.use((req, res, next) => {
    const identity = identities[String(req.headers['x-test-identity'])];
    if (identity === undefined) {
      res.status(401).end();
      return;
    }
    Object.assign(req, identity);
    const party = req.header(PARTY_HEADER);
    if (party === undefined) {
      next();
      return;
    }
    void asParty(party, async () => {
      next();
    });
  });
  const groups = options.modelGroups !== undefined ? { modelGroups: options.modelGroups } : {};
  const chat = chatTurnMiddleware(service, { kind: 'chat', surface: publicSurface, enabled, ...groups });
  const agent = chatTurnMiddleware(service, { kind: 'agent', surface: publicSurface, enabled, ...groups });
  const slackAgent = chatTurnMiddleware(service, { kind: 'agent', surface: slackSurface, identify, enabled, ...groups });
  const chatRegenerate = chatTurnMiddleware(service, { kind: 'chat', surface: publicSurface, enabled, turn: 'regenerate', ...groups });
  const agentRegenerate = chatTurnMiddleware(service, { kind: 'agent', surface: publicSurface, enabled, turn: 'regenerate', ...groups });
  const chatCancel = chatCancelMiddleware(service, { kind: 'chat', enabled });
  const agentCancel = chatCancelMiddleware(service, { kind: 'agent', enabled });
  app.post('/conversations/stream', chat, legacy);
  const send = options.commands?.send ?? ((_req, _res, next) => next());
  const again = options.commands?.regenerate ?? ((_req, _res, next) => next());
  app.post('/conversations/:conversationId/messages/stream', send, chat, legacy);
  app.post('/agents/:agentKey/conversations/stream', agent, legacy);
  app.post('/agents/:agentKey/conversations/:conversationId/messages/stream', agent, legacy);
  app.post('/agents/:agentKey/conversations/internal/stream', slackAgent, legacy);
  app.post('/agents/:agentKey/conversations/internal/:conversationId/messages/stream', slackAgent, legacy);
  app.post('/conversations/:conversationId/message/:messageId/regenerate', again, chatRegenerate, legacy);
  app.post('/agents/:agentKey/conversations/:conversationId/message/:messageId/regenerate', agentRegenerate, legacy);
  app.post('/conversations/:conversationId/cancel', chatCancel, legacy);
  app.post('/agents/:agentKey/conversations/:conversationId/cancel', agentCancel, legacy);
  app.use(ErrorMiddleware.handleError());
  const quiet = [sinon.stub(Logger.getInstance(), 'error'), sinon.stub(Logger.getInstance(), 'warn')];
  const server = await new Promise<Server>((resolve) => {
    const listening = app.listen(0, () => resolve(listening));
  });
  const base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
  return {
    post: (path, identity, body, headers = {}) =>
      fetch(`${base}${path}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-test-identity': identity, authorization: 'Bearer caller', ...headers },
        body: JSON.stringify(body),
      }),
    lines,
    store,
    executions,
    legacy,
    identify,
    submit,
    close: () =>
      new Promise<void>((resolve) => {
        for (const stub of quiet) stub.restore();
        server.close(() => resolve());
      }),
  };
}
