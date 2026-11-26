/**
 * UI-B5 routes over real HTTP: a job's goal history and a request's jobs on both conversation routers. Every
 * successful answer is its contract; the org and the person always come from the token.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import { Container } from 'inversify';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import sinon from 'sinon';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { Logger } from '../../../src/libs/services/logger.service';
import {
  AGENT_HARNESS_JOB_BRIEFS,
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_SESSION_RUN_SERVICE,
} from '../../../src/modules/agent-harness/composition';
import { HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import {
  createAgentHarnessSessionRouter,
  createChatHarnessSessionRouter,
} from '../../../src/modules/agent-harness/transport/session-routes';
import { continueAs } from '../../helpers/request-org-context';
import { assertAnswersContract } from './support/api-contracts';
import { ALICE, ORG } from './support/schedules';

const SESSION = '5f1d7c2e9b1a4c3d2e1f0aaa';

describe('UI-B5 routes (HTTP)', () => {
  let server: Server;
  let base: string;
  let caller: Record<string, unknown>;
  const briefCalls: Array<[string, unknown, string]> = [];
  const quiet: sinon.SinonStub[] = [];

  beforeEach(async () => {
    caller = { userId: ALICE, orgId: ORG };
    briefCalls.length = 0;
    const container = new Container();
    container.bind('AuthMiddleware').toConstantValue({
      authenticate: (
        req: express.Request & { user?: unknown },
        _res: express.Response,
        next: express.NextFunction,
      ) => {
        req.user = { ...caller };
        continueAs(req.user as { orgId: string; userId: string }, next);
      },
    });
    container.bind(AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue({});
    container.bind(AGENT_HARNESS_RUN_ADMISSION_SERVICE).toConstantValue({});
    container.bind(AGENT_HARNESS_JOB_BRIEFS).toConstantValue({
      history: async (
        scope: { orgId: string; sessionId: string },
        runId: string,
      ) => {
        briefCalls.push(['history', scope, runId]);
        if (runId !== 'run-2') throw new HarnessNotFoundError();
        return { runId, startedAs: 'Fix the failing test', changes: [] };
      },
      requestJobs: async (
        scope: { orgId: string; sessionId: string },
        runId: string,
      ) => {
        briefCalls.push(['jobs', scope, runId]);
        return {
          request: {
            runId,
            sessionId: scope.sessionId,
            state: 'working',
            status: 'running',
            ask: 'Compare pricing',
          },
          jobs: [],
          total: 0,
          back: 0,
        };
      },
    });
    quiet.push(
      sinon.stub(Logger.getInstance(), 'error'),
      sinon.stub(Logger.getInstance(), 'warn'),
    );
    const app = express();
    app.use(express.json());
    app.use('/api/v1/conversations', createChatHarnessSessionRouter(container));
    app.use('/api/v1/agents', createAgentHarnessSessionRouter(container));
    app.use(ErrorMiddleware.handleError());
    server = await new Promise<Server>((resolve) => {
      const listening = app.listen(0, () => resolve(listening));
    });
    base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
  });

  afterEach(async () => {
    for (const stub of quiet.splice(0)) stub.restore();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  const call = async (method: string, path: string, body?: unknown) => {
    const url = `${base}${path}`;
    const response = await fetch(url, {
      method,
      headers: { 'content-type': 'application/json' },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    const text = await response.text();
    const json = text ? JSON.parse(text) : null;
    assertAnswersContract(method, url, response.status, json);
    return { status: response.status, json };
  };

  it("B5-R3: a job's history and a request's jobs take the session from the path and the person from the token", async () => {
    const history = await call(
      'GET',
      `/api/v1/conversations/${SESSION}/runs/run-2/brief/history`,
    );
    expect(history.status).to.equal(200);
    const jobs = await call(
      'GET',
      `/api/v1/agents/agent-1/conversations/${SESSION}/runs/run-1/jobs`,
    );
    expect(jobs.status).to.equal(200);
    expect(briefCalls).to.deep.equal([
      ['history', { orgId: ORG, actorId: ALICE, sessionId: SESSION }, 'run-2'],
      [
        'jobs',
        { orgId: ORG, actorId: ALICE, sessionId: SESSION, agentKey: 'agent-1' },
        'run-1',
      ],
    ]);
    expect(
      (
        await call(
          'GET',
          `/api/v1/conversations/${SESSION}/runs/run-9/brief/history`,
        )
      ).status,
    ).to.equal(404);
  });
});
