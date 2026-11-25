/**
 * The public agent-run admission route over real HTTP: the session router
 * with the real `requireScopes` and error middleware, a real
 * RunAdmissionService and the in-memory store. Authentication is stubbed to
 * map a bearer to an identity, as AuthMiddleware would.
 */
import 'reflect-metadata';
import express from 'express';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { Container } from 'inversify';
import sinon from 'sinon';
import { Logger } from '../../../../src/libs/services/logger.service';
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware';
import {
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_SESSION_RUN_SERVICE,
} from '../../../../src/modules/agent-harness/composition';
import {
  createAgentHarnessSessionRouter,
  createChatHarnessSessionRouter,
} from '../../../../src/modules/agent-harness/transport/session-routes';
import { RunAdmissionService } from '../../../../src/modules/agent-harness/application/run-admission-service';
import { continueAs } from '../../../helpers/request-org-context';

export interface Identity {
  orgId: string;
  userId: string;
  isOAuth?: boolean;
  oauthScopes?: string[];
  oauthClientId?: string;
}

export interface Reply {
  status: number;
  headers: Headers;
  body: Record<string, any>;
}

export interface AdmissionHttp {
  post(path: string, bearer: string, body: unknown): Promise<Reply>;
  close(): Promise<void>;
}

export const admissionBody = (
  overrides: Record<string, unknown> = {},
): Record<string, unknown> => ({
  idempotencyKey: `submit-${Math.random().toString(36).slice(2)}`,
  deadlineMs: 600_000,
  query: 'what changed in the release?',
  recordIds: [],
  filters: {},
  attachments: [],
  chatMode: 'auto',
  ...overrides,
});

export async function startAdmissionHttp(
  admissions: RunAdmissionService,
  identities: Record<string, Identity>,
): Promise<AdmissionHttp> {
  const container = new Container();
  container.bind('AuthMiddleware').toConstantValue({
    authenticate: (
      req: express.Request & { user?: Identity },
      res: express.Response,
      next: express.NextFunction,
    ) => {
      const bearer = (req.headers.authorization ?? '').replace(/^Bearer /, '');
      const identity = identities[bearer];
      if (identity === undefined) {
        res.status(401).json({ error: { message: 'unauthenticated' } });
        return;
      }
      req.user = identity;
      continueAs(req.user, next);
    },
  });
  container.bind(AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue({});
  container
    .bind(AGENT_HARNESS_RUN_ADMISSION_SERVICE)
    .toConstantValue(admissions);
  // Expected 4xx/5xx replies; the error middleware would log each one.
  const quiet = [
    sinon.stub(Logger.getInstance(), 'error'),
    sinon.stub(Logger.getInstance(), 'warn'),
  ];
  const app = express();
  app.use(express.json({ limit: '10mb' }));
  app.use('/agents', createAgentHarnessSessionRouter(container));
  app.use('/conversations', createChatHarnessSessionRouter(container));
  app.use(ErrorMiddleware.handleError());
  const server = await new Promise<Server>((resolve) => {
    const listening = app.listen(0, () => resolve(listening));
  });
  const base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
  return {
    post: async (path, bearer, body) => {
      const response = await fetch(`${base}${path}`, {
        method: 'POST',
        headers: {
          authorization: `Bearer ${bearer}`,
          'content-type': 'application/json',
        },
        body: JSON.stringify(body),
      });
      return {
        status: response.status,
        headers: response.headers,
        body: (await response.json()) as Record<string, any>,
      };
    },
    close: () =>
      new Promise<void>((resolve) => {
        for (const stub of quiet) stub.restore();
        server.close(() => resolve());
      }),
  };
}
