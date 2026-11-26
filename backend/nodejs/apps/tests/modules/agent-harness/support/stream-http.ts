/**
 * The durable stream route over real HTTP: the session router with the real
 * scope checks and error middleware, the real SessionRunService deciding who
 * may watch, and the stream service over a golden journal. `owner` is the
 * run's actor, `viewer` a participant the conversation is shared with.
 */
import 'reflect-metadata';
import express from 'express';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { Container } from 'inversify';
import sinon from 'sinon';
import { Logger } from '../../../../src/libs/services/logger.service';
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware';
import { OAuthScopeNames } from '../../../../src/libs/enums/oauth-scopes.enum';
import {
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_RUN_STREAMS,
  AGENT_HARNESS_SESSION_RUN_SERVICE,
} from '../../../../src/modules/agent-harness/composition';
import {
  createAgentHarnessSessionRouter,
  createChatHarnessSessionRouter,
} from '../../../../src/modules/agent-harness/transport/session-routes';
import { SessionRunService } from '../../../../src/modules/agent-harness/application/session-run-service';
import { ExecutionService } from '../../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../../src/modules/agent-harness/application/journal-service';
import { InMemoryPayloadStore } from '../../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { InMemoryJournalTail } from '../../../../src/modules/agent-harness/adapters/in-memory-journal-tail';
import { RunStreamService } from '../../../../src/modules/agent-harness/projection/stream-service';
import { HarnessRun } from '../../../../src/modules/agent-harness/domain/execution';
import { ChatSessionRepository } from '../../../../src/modules/agent-harness/ports/chat-session-repository';
import { GoldenJournal } from '../../../contracts/support/durable';
import { testCipher } from './data-protection';
import { InMemoryRecordReadAccess } from '../../../../src/modules/agent-harness/adapters/in-memory-record-access';
import { SharedCitationService } from '../../../../src/modules/agent-harness/application/shared-citations';
import { finishedConversation } from './citations';
import { continueAs } from '../../../helpers/request-org-context';

export const STREAM_ORG = 'org-stream';

const identities: Record<string, Record<string, unknown>> = {
  owner: { orgId: STREAM_ORG, userId: 'user-owner' },
  viewer: { orgId: STREAM_ORG, userId: 'user-viewer' },
  stranger: { orgId: STREAM_ORG, userId: 'user-stranger' },
  'exec-only-pat': { orgId: STREAM_ORG, userId: 'user-owner', isOAuth: true, oauthScopes: [OAuthScopeNames.AGENT_EXECUTE] },
};

/** A run of `journal` the owner (`user-owner`) started. */
export const streamRun = (journal: GoldenJournal): HarnessRun =>
  ({
    runId: journal.runId,
    orgId: STREAM_ORG,
    sessionId: journal.sessionId,
    actorId: 'user-owner',
    agentKey: 'agent-1',
    harnessMajor: 2,
    status: 'completed',
    input: { query: journal.query },
    createdAt: 1,
    updatedAt: 2,
  }) as unknown as HarnessRun;

export interface Served {
  journal: GoldenJournal;
  run: HarnessRun;
  records: InMemoryRecordReadAccess;
  citations: unknown[];
  chat: boolean;
}

export async function startStreamHttp(
  served: Served,
): Promise<{ get: (path: string, bearer: string, headers?: Record<string, string>) => Promise<Response>; close: () => Promise<void> }> {
  const { journal, run, records, citations: cited } = served;
  const sessions = {
    findStreamAccess: (scope: { actorId: string; sessionId: string }) =>
      Promise.resolve(
        scope.sessionId !== journal.sessionId
          ? null
          : scope.actorId === 'user-owner'
            ? { audience: 'owner', title: journal.query }
            : scope.actorId === 'user-viewer'
              ? { audience: 'shared', title: journal.query }
              : null,
      ),
  } as unknown as ChatSessionRepository;
  const executions = {
    get: (_org: string, runId: string) => Promise.resolve(runId === run.runId ? run : null),
  } as unknown as ExecutionService;
  const streams = new RunStreamService({
    journal: { readRun: (_o, runId, after, limit) => Promise.resolve(journal.events.filter((e) => e.runId === runId && e.seq > after).slice(0, limit)) },
    journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
    tail: new InMemoryJournalTail(),
    conversations: { finishedConversation: () => Promise.resolve(finishedConversation(journal.sessionId, cited)) },
    liveBus: null,
    sharedCitations: new SharedCitationService(records),
  });
  const container = new Container();
  container.bind('AuthMiddleware').toConstantValue({
    authenticate: (req: express.Request & { user?: unknown }, res: express.Response, next: express.NextFunction) => {
      const identity = identities[(req.headers.authorization ?? '').replace(/^Bearer /, '')];
      if (identity === undefined) {
        res.status(401).json({});
        return;
      }
      req.user = identity;
      continueAs(req.user, next);
    },
  });
  container.bind(AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue(new SessionRunService(executions, sessions));
  container.bind(AGENT_HARNESS_RUN_ADMISSION_SERVICE).toConstantValue({});
  container.bind(AGENT_HARNESS_RUN_STREAMS).toConstantValue(streams);
  const quiet = [sinon.stub(Logger.getInstance(), 'error'), sinon.stub(Logger.getInstance(), 'warn')];
  const app = express();
  if (served.chat) app.use('/conversations', createChatHarnessSessionRouter(container));
  else app.use('/agents', createAgentHarnessSessionRouter(container));
  app.use(ErrorMiddleware.handleError());
  const server = await new Promise<Server>((resolve) => {
    const listening = app.listen(0, () => resolve(listening));
  });
  const base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
  return {
    get: (path, bearer, headers = {}) => fetch(`${base}${path}`, { headers: { authorization: `Bearer ${bearer}`, ...headers } }),
    close: () =>
      new Promise<void>((resolve) => {
        for (const stub of quiet) stub.restore();
        server.close(() => resolve());
      }),
  };
}

