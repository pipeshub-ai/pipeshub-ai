/// <reference types="mocha" />
import 'reflect-metadata';
import express, { NextFunction, Request, Response } from 'express';
import type { Server } from 'http';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import mongoose from 'mongoose';
import { createNotificationRouter } from '../../../src/modules/notification/routes/notification.routes';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { COLLAB_TYPES } from '../../../src/modules/enterprise_search/services/collaboration/collab.types';
import { COLLAB_FLAG_KEYS } from '../../../src/modules/configuration_manager/constants/constants';
import {
  MongoNotificationPreferencesRepository,
  defaultNotificationPreferences,
} from '../../../src/modules/notification/repository/notification-preferences.repository';
import {
  TIPS_SEEN_MAX,
  TIP_IDS,
  UserNotificationPreferences,
} from '../../../src/modules/notification/schema/user-notification-preferences.schema';

const TIPS = '/api/v1/notifications/preferences/tips';
const oid = () => new mongoose.Types.ObjectId();

describe('MN-19 notification/preferences tips (route)', () => {
  let app: express.Express;
  let server: Server | undefined;
  let flags: Record<string, boolean>;
  let markTipSeen: sinon.SinonStub;

  beforeEach(() => {
    flags = { [COLLAB_FLAG_KEYS.collaborativeChats]: true, [COLLAB_FLAG_KEYS.chatMentions]: true };
    markTipSeen = sinon
      .stub(MongoNotificationPreferencesRepository.prototype, 'markTipSeen')
      .resolves({ ...defaultNotificationPreferences(), tipsSeen: ['mentions.firstNote'] });
    const container = new Container();
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({
      authenticate: (req: Request, _res: Response, next: NextFunction) => {
        (req as Request & { user: unknown }).user = { userId: String(oid()), orgId: String(oid()) };
        next();
      },
    } as unknown as AuthMiddleware);
    container.bind(COLLAB_TYPES.FeatureFlags).toConstantValue({ isEnabled: async (key: string) => flags[key] === true });
    container.bind(COLLAB_TYPES.ConversationGuards).toConstantValue({ authorize: () => (_q: unknown, _s: unknown, n: () => void) => n() });
    app = express();
    app.use(express.json());
    app.use('/api/v1/notifications', createNotificationRouter(container));
    app.use(ErrorMiddleware.handleError());
  });

  afterEach(async () => {
    sinon.restore();
    if (server) await new Promise<void>((resolve) => server!.close(() => resolve()));
    server = undefined;
  });

  async function patch(body: unknown): Promise<{ status: number; body: any }> {
    const port = await new Promise<number>((resolve) => {
      server = app.listen(0, () => resolve((server!.address() as { port: number }).port));
    });
    const res = await fetch(`http://127.0.0.1:${port}${TIPS}`, {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    return { status: res.status, body: await res.json().catch(() => undefined) };
  }

  it('records a known tip and returns the preferences', async () => {
    const res = await patch({ tipId: 'mentions.firstNote' });
    expect(res.status).to.equal(200);
    expect(res.body.tipsSeen).to.deep.equal(['mentions.firstNote']);
    expect(markTipSeen.firstCall.args[2]).to.equal('mentions.firstNote');
  });

  for (const [name, body] of [
    ['an unknown tip id', { tipId: 'mentions.nope' }],
    ['a missing tip id', {}],
    ['an extra field', { tipId: 'mentions.firstNote', extra: 1 }],
    ['a non-string tip id', { tipId: 7 }],
  ] as const) {
    it(`rejects ${name} with 400 and writes nothing`, async () => {
      const res = await patch(body);
      expect(res.status).to.equal(400);
      expect(markTipSeen.called).to.equal(false);
    });
  }

  it('is 404 with the mentions flag off even though collaborative chats is on', async () => {
    flags[COLLAB_FLAG_KEYS.chatMentions] = false;
    expect((await patch({ tipId: 'mentions.firstNote' })).status).to.equal(404);
    expect(markTipSeen.called).to.equal(false);
  });

  it('is 404 with collaborative chats off', async () => {
    flags[COLLAB_FLAG_KEYS.collaborativeChats] = false;
    expect((await patch({ tipId: 'mentions.firstNote' })).status).to.equal(404);
    expect(markTipSeen.called).to.equal(false);
  });
});

describe('notification preferences tips defaults and schema', () => {
  it('defaults hold when the document is absent', async () => {
    sinon.stub(UserNotificationPreferences, 'findOne').returns({ lean: () => ({ exec: async () => null }) } as never);
    const prefs = await new MongoNotificationPreferencesRepository().get(String(oid()), String(oid()));
    sinon.restore();
    expect(prefs.tipsSeen).to.deep.equal([]);
    expect(prefs.inApp.chatMentioned).to.equal(true);
    expect(prefs.email.chatMentioned).to.equal(false);
  });

  it('defaults hold for a stored document that predates the fields', async () => {
    const stored = { email: { chatShared: false }, inApp: {}, mutedSessions: [] };
    sinon.stub(UserNotificationPreferences, 'findOne').returns({ lean: () => ({ exec: async () => stored }) } as never);
    const prefs = await new MongoNotificationPreferencesRepository().get(String(oid()), String(oid()));
    sinon.restore();
    expect(prefs.tipsSeen).to.deep.equal([]);
    expect(prefs.inApp.chatMentioned).to.equal(true);
    expect(prefs.email.chatMentioned).to.equal(false);
    expect(prefs.email.chatShared).to.equal(false);
  });

  it('enforces the enum and the cap on tipsSeen', () => {
    const mk = (tipsSeen: unknown[]) => new UserNotificationPreferences({ orgId: oid(), userId: oid(), tipsSeen });
    expect(mk([...TIP_IDS]).validateSync()).to.equal(undefined);
    expect(mk(['mentions.bogus']).validateSync()).to.exist;
    expect(mk(Array.from({ length: TIPS_SEEN_MAX + 1 }, () => TIP_IDS[0])).validateSync()!.errors.tipsSeen).to.exist;
  });
});

// Needs a mongod, e.g. PCC_MONGO_URI='mongodb://127.0.0.1:27091/tips_test?directConnection=true'.
const uri = process.env.PCC_MONGO_URI;

(uri ? describe : describe.skip)('MN-19 markTipSeen against a real MongoDB', function () {
  this.timeout(30_000);
  const repo = new MongoNotificationPreferencesRepository();
  const orgId = oid();

  before(async () => {
    await mongoose.connect(uri as string);
    await UserNotificationPreferences.init();
  });
  after(async () => {
    await UserNotificationPreferences.deleteMany({ orgId });
    await mongoose.disconnect();
  });

  it('50 concurrent marks of one tip for a brand-new user leave exactly one entry', async () => {
    const userId = String(oid());
    await Promise.all(Array.from({ length: 50 }, () => repo.markTipSeen(String(orgId), userId, 'mentions.firstNote')));
    const prefs = await repo.get(String(orgId), userId);
    expect(prefs.tipsSeen).to.deep.equal(['mentions.firstNote']);
    expect(await UserNotificationPreferences.countDocuments({ orgId, userId })).to.equal(1);
  });

  it('concurrent marks of different tips keep each exactly once', async () => {
    const userId = String(oid());
    await Promise.all(
      Array.from({ length: 40 }, (_, i) => repo.markTipSeen(String(orgId), userId, TIP_IDS[i % TIP_IDS.length]!)),
    );
    const prefs = await repo.get(String(orgId), userId);
    expect([...prefs.tipsSeen].sort()).to.deep.equal([...TIP_IDS].sort());
  });

  it('does not touch other preference switches', async () => {
    const userId = String(oid());
    await repo.update(String(orgId), userId, { email: { chatShared: false } });
    const prefs = await repo.markTipSeen(String(orgId), userId, 'mentions.popoverIntro');
    expect(prefs.email.chatShared).to.equal(false);
    expect(prefs.tipsSeen).to.deep.equal(['mentions.popoverIntro']);
  });

  it('ignores a new tip once the list is at the cap', async () => {
    const userId = new mongoose.Types.ObjectId();
    await UserNotificationPreferences.collection.insertOne({
      orgId,
      userId,
      tipsSeen: Array.from({ length: TIPS_SEEN_MAX }, (_, i) => `legacy.${String(i)}`),
    });
    const prefs = await repo.markTipSeen(String(orgId), String(userId), 'mentions.firstNote');
    expect(prefs.tipsSeen).to.have.length(TIPS_SEEN_MAX);
    expect(prefs.tipsSeen).to.not.include('mentions.firstNote');
  });

  it('a different user in the same org is unaffected', async () => {
    const a = String(oid());
    const b = String(oid());
    await repo.markTipSeen(String(orgId), a, 'mentions.firstNote');
    expect((await repo.get(String(orgId), b)).tipsSeen).to.deep.equal([]);
  });
});
