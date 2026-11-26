import 'reflect-metadata';
import express from 'express';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { expect } from 'chai';
import { SessionRunService } from '../../../../src/modules/agent-harness/application/session-run-service';
import { ExecutionService } from '../../../../src/modules/agent-harness/application/execution-service';
import { ChatSessionRepository } from '../../../../src/modules/agent-harness/ports/chat-session-repository';
import { activeRunIdMiddleware } from '../../../../src/modules/agent-harness/transport/active-run';

/** L-21: `conversation.activeRunId` on the conversation GET of a durable session only. */
describe('agent-harness: activeRunId on the conversation GET (L-21)', () => {
  let server: Server;
  let base: string;
  let lookups = 0;
  let enabled = true;
  const durable = new Set(['ses-durable', 'ses-idle']);

  before(async () => {
    const sessions = {
      findStreamAccess: (scope: { sessionId: string }) => {
        lookups += 1;
        return Promise.resolve(durable.has(scope.sessionId) ? { audience: 'owner' } : null);
      },
    } as unknown as ChatSessionRepository;
    const executions = {
      getActive: (_org: string, sessionId: string) =>
        Promise.resolve(sessionId === 'ses-durable' ? { runId: 'run-active', sessionId } : null),
    } as unknown as ExecutionService;
    const app = express();
    app.use((req, _res, next) => {
      (req as unknown as { user: unknown }).user = { orgId: 'org-1', userId: 'user-1' };
      next();
    });
    app.get(
      '/conversations/:conversationId',
      activeRunIdMiddleware(new SessionRunService(executions, sessions), 'chat', () => enabled),
      (req, res) => {
        res.status(200).json({ conversation: { _id: req.params['conversationId'], messages: [] }, meta: {} });
      },
    );
    server = await new Promise<Server>((resolve) => {
      const listening = app.listen(0, () => resolve(listening));
    });
    base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
  });

  after(() => new Promise<void>((resolve) => server.close(() => resolve())));

  const conversation = async (id: string): Promise<Record<string, unknown>> =>
    ((await (await fetch(`${base}/conversations/${id}`)).json()) as { conversation: Record<string, unknown> }).conversation;

  it('a durable session with a run in progress names it', async () => {
    expect((await conversation('ses-durable'))['activeRunId']).to.equal('run-active');
  });

  it('a durable session with nothing running says null', async () => {
    const body = await conversation('ses-idle');
    expect(body).to.have.property('activeRunId', null);
  });

  it('a legacy session keeps its response exactly as it was', async () => {
    expect(await conversation('ses-legacy')).to.deep.equal({ _id: 'ses-legacy', messages: [] });
  });

  it('with the harness off nothing is looked up or added', async () => {
    enabled = false;
    lookups = 0;
    try {
      expect(await conversation('ses-durable')).to.deep.equal({ _id: 'ses-durable', messages: [] });
      expect(lookups).to.equal(0);
    } finally {
      enabled = true;
    }
  });
});
