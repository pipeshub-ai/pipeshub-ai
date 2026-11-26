/**
 * SLASH3 (QA-26): every sandbox route resolves the session within the caller's org and requires its owner before it
 * reads a byte of the body or query, so a stranger learns nothing from validation (400), a missing feature (500) or
 * a size limit (413): only 404.
 */
import 'reflect-metadata';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { expect } from 'chai';
import express from 'express';
import sinon from 'sinon';
import { Container } from 'inversify';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import { HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import {
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_SESSION_RUN_SERVICE,
} from '../../../src/modules/agent-harness/composition';
import {
  createAgentHarnessSessionRouter,
  createChatHarnessSessionRouter,
} from '../../../src/modules/agent-harness/transport/session-routes';

const ORG = '64b000000000000000000001';
const USER = '64b000000000000000000002';

/** Each route with a body or query no handler would accept; the services are not bound at all. */
const ROUTES: Array<{ method: string; path: string; init?: RequestInit }> = [
  { method: 'GET', path: '/workspace/files?revision=minus-one&extra=1' },
  { method: 'GET', path: '/workspace/file' },
  { method: 'GET', path: '/workspace/archive?revision=x' },
  { method: 'POST', path: '/workspace/files', init: { body: 'not multipart', headers: { 'content-type': 'text/plain' } } },
  { method: 'POST', path: '/commands/run', init: { body: '{"orgId":"x"}', headers: { 'content-type': 'application/json' } } },
  { method: 'PUT', path: '/coding-engine', init: { body: '{"engine":7}', headers: { 'content-type': 'application/json' } } },
  { method: 'GET', path: '/sandbox?x=1' },
  { method: 'DELETE', path: '/sandbox/kept?x=1' },
];

describe('SLASH3: sandbox routes check the owner before the request (QA-26)', () => {
  let server: Server;
  let base: string;
  let originalLogger: unknown;
  const owners: unknown[] = [];

  before(async () => {
    const container = new Container();
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({
      authenticate: (req: any, _res: unknown, next: () => void) => {
        req.user = { orgId: ORG, userId: USER };
        next();
      },
    } as never);
    container.bind(AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue({
      assertOwner: async (scope: unknown) => {
        owners.push(scope);
        throw new HarnessNotFoundError();
      },
    });
    container.bind(AGENT_HARNESS_RUN_ADMISSION_SERVICE).toConstantValue({});
    const app = express();
    app.use('/api/v1/conversations', createChatHarnessSessionRouter(container));
    app.use('/api/v1/agents', createAgentHarnessSessionRouter(container));
    app.use(ErrorMiddleware.handleError());
    originalLogger = (ErrorMiddleware as any).logger;
    (ErrorMiddleware as any).logger = { error: sinon.stub(), warn: sinon.stub(), info: sinon.stub(), debug: sinon.stub() };
    server = await new Promise<Server>((resolve) => {
      const listening: Server = app.listen(0, () => resolve(listening));
    });
    base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}/api/v1`;
  });

  after(async () => {
    (ErrorMiddleware as any).logger = originalLogger;
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  for (const prefix of ['/conversations/s-a', '/agents/agent-a/conversations/s-a']) {
    for (const route of ROUTES) {
      it(`${route.method} ${prefix}${route.path.split('?')[0]!}: a stranger's malformed request is 404, nothing more`, async () => {
        owners.length = 0;
        const res = await fetch(`${base}${prefix}${route.path}`, { method: route.method, ...route.init }).catch(
          (error: unknown) => error as Error,
        );
        if (res instanceof Error) throw res;
        expect(res.status).to.equal(404);
        expect(JSON.stringify(await res.json())).not.to.match(/upload|engine|configured|larger/i);
        expect(owners).to.deep.equal([
          { orgId: ORG, actorId: USER, sessionId: 's-a', ...(prefix.startsWith('/agents') ? { agentKey: 'agent-a' } : {}) },
        ]);
      });
    }
  }
});
