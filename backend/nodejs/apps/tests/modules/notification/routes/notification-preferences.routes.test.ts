/// <reference types="mocha" />
import 'reflect-metadata';
import express, { NextFunction, Request, RequestHandler, Response } from 'express';
import type { Server } from 'http';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import mongoose from 'mongoose';
import { createNotificationRouter } from '../../../../src/modules/notification/routes/notification.routes';
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware';
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware';
import { COLLAB_TYPES } from '../../../../src/modules/enterprise_search/services/collaboration/collab.types';
import { ConversationNotFoundError } from '../../../../src/modules/enterprise_search/services/collaboration/domain/errors';
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema';
import {
  MongoNotificationPreferencesRepository,
  NotificationPreferences,
} from '../../../../src/modules/notification/repository/notification-preferences.repository';
import { MUTED_SESSIONS_MAX } from '../../../../src/modules/notification/schema/user-notification-preferences.schema';

const PREFS = '/api/v1/notifications/preferences';

describe('notification/routes preferences', () => {
  let app: express.Express;
  let server: Server | undefined;
  let userId: string;
  let orgId: string;
  let sessionId: string;
  let readable: boolean;
  let flagOn: boolean;
  let guardCalls: { op: string; kind: string; params: Record<string, string | undefined> }[];
  let repo: {
    get: sinon.SinonStub;
    update: sinon.SinonStub;
    muteSession: sinon.SinonStub;
    unmuteSession: sinon.SinonStub;
  };
  let sessionLookup: sinon.SinonStub;
  const stored: NotificationPreferences = {
    email: { chatShared: false, ownershipTransferred: true, chatMentioned: false },
    inApp: { chatActivity: true, chatMentioned: true },
    mutedSessions: [],
    tipsSeen: [],
  };

  beforeEach(() => {
    userId = new mongoose.Types.ObjectId().toString();
    orgId = new mongoose.Types.ObjectId().toString();
    sessionId = new mongoose.Types.ObjectId().toString();
    readable = true;
    flagOn = true;
    guardCalls = [];
    repo = {
      get: sinon.stub().resolves(stored),
      update: sinon.stub().resolves(stored),
      muteSession: sinon.stub().resolves('muted'),
      unmuteSession: sinon.stub().resolves(),
    };
    for (const name of Object.keys(repo) as (keyof typeof repo)[]) {
      sinon.stub(MongoNotificationPreferencesRepository.prototype, name).callsFake(repo[name] as never);
    }
    sessionLookup = sinon.stub();
    sinon.stub(ChatSession, 'findOne').returns({
      select: () => ({ lean: sessionLookup }),
    } as never);

    const container = new Container();
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({
      authenticate: (req: Request, _res: Response, next: NextFunction) => {
        (req as Request & { user: unknown }).user = { userId, orgId };
        next();
      },
    } as unknown as AuthMiddleware);
    container.bind(COLLAB_TYPES.FeatureFlags).toConstantValue({ isEnabled: async () => flagOn });
    container.bind(COLLAB_TYPES.ConversationGuards).toConstantValue({
      authorize: (op: string, kind: string): RequestHandler => (req, _res, next) => {
        guardCalls.push({ op, kind, params: { ...req.params } });
        next(readable ? undefined : new ConversationNotFoundError());
      },
    });

    app = express();
    app.use(express.json());
    app.use('/api/v1/notifications', createNotificationRouter(container));
    app.use(ErrorMiddleware.handleError());
  });

  afterEach(async () => {
    sinon.restore();
    if (server) {
      await new Promise<void>((resolve, reject) => {
        server!.close((err) => (err ? reject(err) : resolve()));
      });
      server = undefined;
    }
  });

  async function call(method: string, path: string, body?: unknown): Promise<{ status: number; body: any }> {
    const port = await new Promise<number>((resolve) => {
      server = app.listen(0, () => resolve((server!.address() as { port: number }).port));
    });
    const res = await fetch(`http://127.0.0.1:${port}${path}`, {
      method,
      headers: { 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return { status: res.status, body: await res.json().catch(() => undefined) };
  }

  it('every preference route is 404 with the collaborative chats flag off, before any store read', async () => {
    flagOn = false;
    for (const [method, path] of [
      ['GET', PREFS],
      ['PATCH', PREFS],
      ['PUT', `${PREFS}/muted-sessions/${sessionId}`],
      ['DELETE', `${PREFS}/muted-sessions/${sessionId}`],
    ] as const) {
      const res = await call(method, path, method === 'PATCH' ? { inApp: { chatActivity: false } } : undefined);
      expect(res.status, `${method} ${path}`).to.equal(404);
      server?.close();
      server = undefined;
    }
    expect(Object.values(repo).some((stub) => stub.called)).to.equal(false);
    expect(guardCalls).to.deep.equal([]);
  });

  describe('GET /preferences', () => {
    it('returns the caller preferences, scoped by org and user', async () => {
      const res = await call('GET', PREFS);
      expect(res.status).to.equal(200);
      expect(res.body).to.deep.equal(stored);
      expect(repo.get.calledOnceWith(orgId, userId)).to.be.true;
    });
  });

  describe('PATCH /preferences', () => {
    it('applies a partial update and returns the result', async () => {
      const res = await call('PATCH', PREFS, { email: { chatShared: false }, inApp: { chatActivity: false } });
      expect(res.status).to.equal(200);
      expect(repo.update.calledOnceWith(orgId, userId, { email: { chatShared: false }, inApp: { chatActivity: false } })).to.be.true;
      expect(res.body).to.deep.equal(stored);
    });

    for (const [name, body] of [
      ['an empty body', {}],
      ['empty groups', { email: {}, inApp: {} }],
      ['a non-boolean value', { email: { chatShared: 'no' } }],
      ['an unknown group', { push: { enabled: true } }],
      ['an unknown key in a group', { email: { chatShared: true, digest: true } }],
    ] as const) {
      it(`rejects ${name} with 400 and writes nothing`, async () => {
        const res = await call('PATCH', PREFS, body);
        expect(res.status).to.equal(400);
        expect(repo.update.called).to.be.false;
      });
    }
  });

  describe('muted sessions', () => {
    const path = () => `${PREFS}/muted-sessions/${sessionId}`;

    it('PUT mutes after the chat read guard passes', async () => {
      sessionLookup.resolves({ sessionType: 'chat' });
      const res = await call('PUT', path());
      expect(res.status).to.equal(200);
      expect(guardCalls).to.have.length(1);
      expect(guardCalls[0]).to.deep.include({ op: 'read', kind: 'chat' });
      expect(guardCalls[0]!.params.conversationId).to.equal(sessionId);
      expect(repo.muteSession.calledOnceWith(orgId, userId, sessionId)).to.be.true;
    });

    it('PUT on an agent conversation authorizes as an agent with its agentKey', async () => {
      sessionLookup.resolves({ sessionType: 'agent', agentKey: 'agent-1' });
      const res = await call('PUT', path());
      expect(res.status).to.equal(200);
      expect(guardCalls[0]).to.deep.include({ op: 'read', kind: 'agent' });
      expect(guardCalls[0]!.params.agentKey).to.equal('agent-1');
    });

    it('PUT on a session the caller cannot read is 404 and mutes nothing', async () => {
      sessionLookup.resolves(null);
      readable = false;
      const res = await call('PUT', path());
      expect(res.status).to.equal(404);
      expect(res.body.error.code).to.equal('CONVERSATION_NOT_FOUND');
      expect(repo.muteSession.called).to.be.false;
    });

    it('PUT past the cap is 409 MUTED_SESSIONS_LIMIT with the max', async () => {
      sessionLookup.resolves(null);
      repo.muteSession.resolves('limit');
      const res = await call('PUT', path());
      expect(res.status).to.equal(409);
      expect(res.body.error.code).to.equal('MUTED_SESSIONS_LIMIT');
      expect(res.body.error.details).to.deep.equal({ max: MUTED_SESSIONS_MAX });
    });

    it('PUT on an already muted session is a 200 no-op', async () => {
      sessionLookup.resolves(null);
      repo.muteSession.resolves('already_muted');
      expect((await call('PUT', path())).status).to.equal(200);
    });

    it('DELETE unmutes without reading the conversation', async () => {
      sessionLookup.resolves(null);
      const res = await call('DELETE', path());
      expect(res.status).to.equal(200);
      expect(guardCalls).to.have.length(0);
      expect(repo.unmuteSession.calledOnceWith(orgId, userId, sessionId)).to.be.true;
    });

    it('DELETE still clears a muted session the caller can no longer read', async () => {
      sessionLookup.resolves(null);
      readable = false;
      expect((await call('DELETE', path())).status).to.equal(200);
      expect(repo.unmuteSession.calledOnceWith(orgId, userId, sessionId)).to.be.true;
    });

    it('rejects a malformed session id before the guard runs', async () => {
      const res = await call('PUT', `${PREFS}/muted-sessions/not-an-id`);
      expect(res.status).to.equal(400);
      expect(guardCalls).to.have.length(0);
    });
  });
});
