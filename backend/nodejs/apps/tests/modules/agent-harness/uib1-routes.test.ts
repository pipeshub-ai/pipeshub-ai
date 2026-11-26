/**
 * UI-B1 routes over real HTTP (ADR-UI-01, ADR-GRANT-01 D8): the caller's runs, a job's brief, the seen mark, the
 * conversation's grants, and the chat list's run state. The org and the person always come from the token; the path
 * and the query can only narrow what the caller already owns.
 */
import 'reflect-metadata';
import express from 'express';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { Container } from 'inversify';
import sinon from 'sinon';
import { expect } from 'chai';
import { Logger } from '../../../src/libs/services/logger.service';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import {
  AGENT_HARNESS_CONVERSATION_GRANTS,
  AGENT_HARNESS_JOB_BRIEFS,
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_RUN_LISTS,
  AGENT_HARNESS_SESSION_RUN_SERVICE,
} from '../../../src/modules/agent-harness/composition';
import {
  createAgentHarnessSessionRouter,
  createChatHarnessSessionRouter,
} from '../../../src/modules/agent-harness/transport/session-routes';
import { createAgentHarnessRunsRouter } from '../../../src/modules/agent-harness/transport/run-list-routes';
import { conversationRunStatesMiddleware } from '../../../src/modules/agent-harness/transport/conversation-run-states';
import { HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import { encodeRunListCursor } from '../../../src/modules/agent-harness/domain/run-lists';
import { personRunsSchema } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/person-runs';
import { conversationRunStateSchema } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/conversation-run-state';
import { continueAs } from '../../helpers/request-org-context';

interface Identity {
  orgId: string;
  userId: string;
  isOAuth?: boolean;
  oauthScopes?: string[];
}

const ORG = '5f1d7c2e9b1a4c3d2e1f0a9b';
const USER = '5f1d7c2e9b1a4c3d2e1f0a9c';
const SESSION = '5f1d7c2e9b1a4c3d2e1f0aaa';
const GRANT = `cg_${'1'.repeat(40)}`;

describe('UI-B1 routes (ADR-UI-01, ADR-GRANT-01 D8)', () => {
  let server: Server;
  let base: string;
  const calls: Array<[string, unknown, unknown?]> = [];
  const quiet: sinon.SinonStub[] = [];

  before(async () => {
    const container = new Container();
    const identities: Record<string, Identity> = {
      owner: { orgId: ORG, userId: USER },
      'oauth-no-read': { orgId: ORG, userId: USER, isOAuth: true, oauthScopes: ['agent:execute'] },
    };
    container.bind('AuthMiddleware').toConstantValue({
      authenticate: (req: express.Request & { user?: Identity }, res: express.Response, next: express.NextFunction) => {
        const identity = identities[(req.headers.authorization ?? '').replace(/^Bearer /, '')];
        if (identity === undefined) {
          res.status(401).json({ error: { message: 'unauthenticated' } });
          return;
        }
        req.user = identity;
        continueAs(req.user, next);
      },
    });
    container.bind(AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue({});
    container.bind(AGENT_HARNESS_RUN_ADMISSION_SERVICE).toConstantValue({});
    container.bind(AGENT_HARNESS_RUN_LISTS).toConstantValue({
      personRuns: async (caller: unknown, filter: unknown) => {
        calls.push(['personRuns', caller, filter]);
        return { runs: [], nextCursor: null };
      },
      markSeen: async (scope: unknown) => {
        calls.push(['markSeen', scope]);
        return { seenUpTo: 7_000 };
      },
    });
    container.bind(AGENT_HARNESS_JOB_BRIEFS).toConstantValue({
      brief: async (scope: unknown, runId: string) => {
        calls.push(['brief', scope, runId]);
        throw new HarnessNotFoundError();
      },
    });
    container.bind(AGENT_HARNESS_CONVERSATION_GRANTS).toConstantValue({
      list: async (scope: unknown) => {
        calls.push(['grants', scope]);
        return [];
      },
      revoke: async (scope: unknown, grantId: string) => {
        calls.push(['revoke', scope, grantId]);
        throw new HarnessNotFoundError('Grant not found');
      },
    });
    quiet.push(sinon.stub(Logger.getInstance(), 'error'), sinon.stub(Logger.getInstance(), 'warn'));
    const app = express();
    app.use(express.json());
    app.use('/api/v1/agent-harness/runs', createAgentHarnessRunsRouter(container));
    app.use('/api/v1/conversations', createChatHarnessSessionRouter(container));
    app.use('/api/v1/agents', createAgentHarnessSessionRouter(container));
    app.use(ErrorMiddleware.handleError());
    server = await new Promise<Server>((resolve) => {
      const listening = app.listen(0, () => resolve(listening));
    });
    base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
  });

  after(async () => {
    for (const stub of quiet) stub.restore();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  beforeEach(() => {
    calls.length = 0;
  });

  const call = async (method: string, path: string, bearer = 'owner', body?: unknown) => {
    const response = await fetch(`${base}${path}`, {
      method,
      headers: { authorization: `Bearer ${bearer}`, 'content-type': 'application/json' },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    return { status: response.status, body: (await response.json()) as Record<string, unknown> };
  };

  it("UIB1-R1: GET /runs lists the token's own person; the query only narrows it", async () => {
    const cursor = encodeRunListCursor({ createdAt: 5_000, runId: 'run-9' });
    const reply = await call('GET', `/api/v1/agent-harness/runs?state=working,needs_you&agentKey=triage&kind=job&from=1&to=9&limit=20&cursor=${cursor}`);
    expect(reply.status).to.equal(200);
    expect(personRunsSchema.safeParse(reply.body).success).to.equal(true);
    expect(calls).to.deep.equal([
      [
        'personRuns',
        { orgId: ORG, actorId: USER },
        { states: ['working', 'needs_you'], agentKey: 'triage', kind: 'job', createdFrom: 1, createdTo: 9, after: { createdAt: 5_000, runId: 'run-9' }, limit: 20 },
      ],
    ]);
  });

  it('UIB1-R2: a state word, cursor or limit the route does not know, and anyone naming another person, is refused', async () => {
    for (const query of ['state=sleeping', 'cursor=forged', 'limit=1000', 'actorId=someone-else', 'orgId=another-org']) {
      expect((await call('GET', `/api/v1/agent-harness/runs?${query}`)).status, query).to.equal(400);
    }
    expect((await call('GET', '/api/v1/agent-harness/runs', 'oauth-no-read')).status).to.equal(403);
    expect((await call('GET', '/api/v1/agent-harness/runs', 'nobody')).status).to.equal(401);
    expect(calls).to.deep.equal([]);
  });

  it('UIB1-R3: the conversation routes take the session from the path and the person from the token', async () => {
    expect((await call('PUT', `/api/v1/conversations/${SESSION}/seen`, 'owner', {})).body).to.deep.equal({ seenUpTo: 7_000 });
    expect((await call('GET', `/api/v1/conversations/${SESSION}/grants`)).body).to.deep.equal({ grants: [] });
    expect((await call('GET', `/api/v1/agents/triage/conversations/${SESSION}/runs/run-2/brief`)).status).to.equal(404);
    expect((await call('DELETE', `/api/v1/conversations/${SESSION}/grants/${GRANT}`)).status).to.equal(404);
    const scope = { orgId: ORG, actorId: USER, sessionId: SESSION };
    expect(calls).to.deep.equal([
      ['markSeen', scope],
      ['grants', scope],
      ['brief', { ...scope, agentKey: 'triage' }, 'run-2'],
      ['revoke', scope, GRANT],
    ]);
  });

  it('UIB1-R4: a seen mark names nothing in its body, and a grant id is only one this server makes', async () => {
    expect((await call('PUT', `/api/v1/conversations/${SESSION}/seen`, 'owner', { at: 9_999_999_999_999 })).status).to.equal(400);
    expect((await call('DELETE', `/api/v1/conversations/${SESSION}/grants/onyx.app`)).status).to.equal(400);
    expect(calls).to.deep.equal([]);
  });

  describe('the chat list run state (ADR-UI-01 D2)', () => {
    const state = {
      sessionId: 's1',
      activeRun: null,
      runState: null,
      needsYou: false,
      finishedUnseen: true,
      seenUpTo: 4_000,
    };
    const decorate = async (
      body: unknown,
      service: { conversationStates: (...args: unknown[]) => Promise<unknown> } | null,
      status = 200,
      user: Record<string, unknown> = { orgId: ORG, userId: USER },
    ) => {
      const middleware = conversationRunStatesMiddleware(() => service as never, () => true);
      let sent: unknown;
      const res = { statusCode: status, json: (value: unknown) => { sent = value; return res; } } as unknown as express.Response;
      void middleware({ user } as unknown as express.Request, res, () => undefined);
      res.json(body);
      await new Promise((resolve) => setImmediate(resolve));
      return sent;
    };

    it("UIB1-R5: each conversation of the caller's list carries its harnessState; the caller's own", async () => {
      const asked: unknown[] = [];
      const sent = await decorate(
        { conversations: [{ _id: 's1', title: 'a' }, { _id: 's2', title: 'b' }], pagination: {} },
        { conversationStates: async (...args: unknown[]) => { asked.push(args); return [state]; } },
      );
      expect(asked).to.deep.equal([[{ orgId: ORG, actorId: USER }, ['s1', 's2']]]);
      const conversations = (sent as { conversations: Array<Record<string, unknown>> }).conversations;
      expect(conversations[0]).to.deep.include({ _id: 's1', title: 'a' });
      expect(conversationRunStateSchema.safeParse(conversations[0]!['harnessState']).success).to.equal(true);
      expect(conversations[1]).to.deep.equal({ _id: 's2', title: 'b' });
      expect(sent).to.have.property('pagination');
    });

    it('UIB1-R6: a list whose states cannot be read, an error answer, or the harness off is answered as it was', async () => {
      const body = { conversations: [{ _id: 's1' }] };
      expect(await decorate(body, { conversationStates: async () => Promise.reject(new Error('down')) })).to.deep.equal(body);
      expect(await decorate(body, { conversationStates: async () => [state] }, 500)).to.deep.equal(body);
      expect(await decorate(body, null)).to.deep.equal(body);
      expect(await decorate(body, { conversationStates: async () => [state] }, 200, {})).to.deep.equal(body);
    });
  });
});
