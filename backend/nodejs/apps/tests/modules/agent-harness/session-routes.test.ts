import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import express from 'express';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import { SCANS_AFTER_AUTH } from '../../../src/modules/agent-harness/transport/pasted-secret-guard';
import {
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_SESSION_RUN_SERVICE,
} from '../../../src/modules/agent-harness/composition';
import {
  createAgentHarnessSessionRouter,
  createChatHarnessSessionRouter,
} from '../../../src/modules/agent-harness/transport/session-routes';

describe('agent harness session routes', () => {
  it('applies user authentication to every public lifecycle operation', () => {
    const container = new Container();
    const authenticate = sinon
      .stub()
      .callsFake((_req: unknown, _res: unknown, next: () => void) => next());
    container
      .bind<AuthMiddleware>('AuthMiddleware')
      .toConstantValue({ authenticate } as never);
    container.bind(AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue({});
    container.bind(AGENT_HARNESS_RUN_ADMISSION_SERVICE).toConstantValue({});
    container.bind('KeyValueStoreService').toConstantValue({});

    const router = createAgentHarnessSessionRouter(container);
    const paths = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => layer.route.path);
    expect(paths).to.deep.equal([
      '/:agentKey/runs',
      '/:agentKey/conversations/:sessionId/runs',
      '/:agentKey/conversations/:sessionId/runs/:runId',
      '/:agentKey/conversations/:sessionId/runs/:runId/resolved',
      '/:agentKey/conversations/:sessionId/runs/:runId/cancel',
      '/:agentKey/conversations/:sessionId/runs/:runId/resume',
      '/:agentKey/conversations/:sessionId/runs/:runId/timeline',
      '/:agentKey/conversations/:sessionId/runs/:runId/cost',
      '/:agentKey/conversations/:sessionId/usage',
      '/:agentKey/conversations/:sessionId/compact',
      '/:agentKey/conversations/:sessionId/runs/:runId/interactions/authority',
      '/:agentKey/conversations/:sessionId/workspace/versions',
      '/:agentKey/conversations/:sessionId/workspace/diff',
      '/:agentKey/conversations/:sessionId/workspace/files',
      '/:agentKey/conversations/:sessionId/workspace/files',
      '/:agentKey/conversations/:sessionId/workspace/file',
      '/:agentKey/conversations/:sessionId/workspace/archive',
      '/:agentKey/conversations/:sessionId/commands/run',
      '/:agentKey/conversations/:sessionId/coding-engine',
      // WP-26: GET, PUT and DELETE of the chat's repository.
      '/:agentKey/conversations/:sessionId/repository',
      '/:agentKey/conversations/:sessionId/repository',
      '/:agentKey/conversations/:sessionId/repository',
      '/:agentKey/conversations/:sessionId/sandbox',
      '/:agentKey/conversations/:sessionId/sandbox/kept',
      '/:agentKey/conversations/:sessionId/workspace/versions/:revision/restore',
      '/:agentKey/conversations/:sessionId/runs/:runId/interactions/:interactionId/decision',
      '/:agentKey/conversations/:sessionId/runs/:runId/interactions/decisions',
      // UI-B1 (ADR-UI-01, ADR-GRANT-01 D8): a job's brief, the seen mark, the grants and a grant's revocation.
      '/:agentKey/conversations/:sessionId/runs/:runId/brief',
      '/:agentKey/conversations/:sessionId/runs/:runId/brief/history',
      '/:agentKey/conversations/:sessionId/runs/:runId/jobs',
      '/:agentKey/conversations/:sessionId/seen',
      '/:agentKey/conversations/:sessionId/grants',
      '/:agentKey/conversations/:sessionId/grants/:grantId',
    ]);
    for (const layer of router.stack.filter((item: any) => item.route)) {
      // SEC-C-01: the authenticator, then the pasted-secret scan, in one first handler.
      const first = layer.route!.stack[0]!.handle as any;
      expect(first[SCANS_AFTER_AUTH]).to.equal(true);
      authenticate.resetHistory();
      first({ body: {} }, {}, () => undefined);
      expect(authenticate.calledOnce).to.equal(true);
    }
  });

  it('exposes the same lifecycle for built-in chat sessions', () => {
    const container = new Container();
    const authenticate = sinon
      .stub()
      .callsFake((_req: unknown, _res: unknown, next: () => void) => next());
    container
      .bind<AuthMiddleware>('AuthMiddleware')
      .toConstantValue({ authenticate } as never);
    container.bind(AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue({});
    container.bind(AGENT_HARNESS_RUN_ADMISSION_SERVICE).toConstantValue({});
    container.bind('KeyValueStoreService').toConstantValue({});

    const router = createChatHarnessSessionRouter(container);
    const paths = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => layer.route.path);
    expect(paths).to.deep.equal([
      '/runs',
      '/:sessionId/runs',
      '/:sessionId/runs/:runId',
      '/:sessionId/runs/:runId/resolved',
      '/:sessionId/runs/:runId/cancel',
      '/:sessionId/runs/:runId/resume',
      '/:sessionId/runs/:runId/timeline',
      '/:sessionId/runs/:runId/cost',
      '/:sessionId/usage',
      '/:sessionId/compact',
      '/:sessionId/runs/:runId/interactions/authority',
      '/:sessionId/workspace/versions',
      '/:sessionId/workspace/diff',
      '/:sessionId/workspace/files',
      '/:sessionId/workspace/files',
      '/:sessionId/workspace/file',
      '/:sessionId/workspace/archive',
      '/:sessionId/commands/run',
      '/:sessionId/coding-engine',
      '/:sessionId/repository',
      '/:sessionId/repository',
      '/:sessionId/repository',
      '/:sessionId/sandbox',
      '/:sessionId/sandbox/kept',
      '/:sessionId/workspace/versions/:revision/restore',
      '/:sessionId/runs/:runId/interactions/:interactionId/decision',
      '/:sessionId/runs/:runId/interactions/decisions',
      '/:sessionId/runs/:runId/brief',
      '/:sessionId/runs/:runId/brief/history',
      '/:sessionId/runs/:runId/jobs',
      '/:sessionId/seen',
      '/:sessionId/grants',
      '/:sessionId/grants/:grantId',
    ]);
    for (const layer of router.stack.filter((item: any) => item.route)) {
      // SEC-C-01: the authenticator, then the pasted-secret scan, in one first handler.
      const first = layer.route!.stack[0]!.handle as any;
      expect(first[SCANS_AFTER_AUTH]).to.equal(true);
      authenticate.resetHistory();
      first({ body: {} }, {}, () => undefined);
      expect(authenticate.calledOnce).to.equal(true);
    }
  });

  it('answers 404 for the removed event cursor and active-run routes (C-8)', async () => {
    const container = new Container();
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({
      authenticate: (req: any, _res: unknown, next: () => void) => {
        req.user = { orgId: 'org-1', userId: 'user-1' };
        next();
      },
    } as never);
    const get = sinon.stub().rejects(new HarnessNotFoundError());
    container.bind(AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue({ get });
    container.bind(AGENT_HARNESS_RUN_ADMISSION_SERVICE).toConstantValue({});
    const app = express();
    app.use('/api/v1/conversations', createChatHarnessSessionRouter(container));
    app.use('/api/v1/agents', createAgentHarnessSessionRouter(container));
    app.use(ErrorMiddleware.handleError());
    const silent = { error: sinon.stub(), warn: sinon.stub(), info: sinon.stub(), debug: sinon.stub() };
    const originalLogger = (ErrorMiddleware as any).logger;
    (ErrorMiddleware as any).logger = silent;
    const server = await new Promise<Server>((resolve) => {
      const listening: Server = app.listen(0, () => resolve(listening));
    });
    const base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}/api/v1`;
    try {
      for (const path of [
        '/conversations/session-1/events',
        '/conversations/session-1/runs/active',
        '/agents/agent-1/conversations/session-1/events',
        '/agents/agent-1/conversations/session-1/runs/active',
      ]) {
        const reply = await fetch(`${base}${path}`);
        expect(reply.status, path).to.equal(404);
      }
      // `active` is only ever read as a run id now, and no such run exists.
      expect(get.args.map((call) => call[1])).to.deep.equal(['active', 'active']);
    } finally {
      (ErrorMiddleware as any).logger = originalLogger;
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
  });

  it('VA-1/VA-2: admission records the verified credential\'s via; a body naming via, org or user is refused', async () => {
    const tokens: Record<string, Record<string, unknown>> = {
      session: { orgId: 'org-1', userId: 'user-1' },
      pat: { orgId: 'org-1', userId: 'user-1', isOAuth: true, oauthClientId: 'pat-system:org-1', oauthScopes: ['conversation:chat'] },
      oauth: { orgId: 'org-1', userId: 'user-1', isOAuth: true, oauthClientId: 'client-9', oauthScopes: ['conversation:chat'] },
      service: { orgId: 'org-1', userId: 'user-1', isServiceAccount: true },
    };
    const container = new Container();
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({
      authenticate: (req: any, _res: unknown, next: () => void) => {
        req.user = tokens[String(req.headers['x-test-token'])];
        next();
      },
    } as never);
    const submitted: Array<Record<string, unknown>> = [];
    const submit = sinon.stub().callsFake(async (request: Record<string, unknown>) => {
      submitted.push(request);
      return { route: 'legacy', rollout: { useHarness: false, harnessMajor: 2, reason: 'org_disabled' } };
    });
    container.bind(AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue({});
    container.bind(AGENT_HARNESS_RUN_ADMISSION_SERVICE).toConstantValue({ submit });
    const app = express();
    app.use(express.json());
    app.use('/api/v1/conversations', createChatHarnessSessionRouter(container));
    app.use(ErrorMiddleware.handleError());
    const originalLogger = (ErrorMiddleware as any).logger;
    (ErrorMiddleware as any).logger = { error: sinon.stub(), warn: sinon.stub(), info: sinon.stub(), debug: sinon.stub() };
    const server = await new Promise<Server>((resolve) => {
      const listening: Server = app.listen(0, () => resolve(listening));
    });
    const base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}/api/v1`;
    try {
      const post = (token: string, extra: Record<string, unknown>) =>
        fetch(`${base}/conversations/runs`, {
          method: 'POST',
          headers: { 'content-type': 'application/json', 'x-test-token': token },
          body: JSON.stringify({ idempotencyKey: `k-${token}`, query: 'hello', chatMode: 'internal_search', ...extra }),
        });
      for (const forged of [{ via: 'trigger' }, { orgId: 'org-attacker' }, { userId: 'user-attacker' }, { actorId: 'user-attacker' }]) {
        expect((await post('session', forged)).status, JSON.stringify(forged)).to.equal(400);
      }
      expect(submit.callCount, 'nothing forged reaches admission').to.equal(0);
      for (const token of Object.keys(tokens)) {
        expect((await post(token, {})).status, token).to.equal(200);
      }
    } finally {
      (ErrorMiddleware as any).logger = originalLogger;
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
    expect(submitted.map((request) => request['via'])).to.deep.equal(['session', 'pat', 'oauth_app', 'service']);
    for (const request of submitted) {
      expect(request).to.include({ orgId: 'org-1', actorId: 'user-1' });
    }
  });
});
