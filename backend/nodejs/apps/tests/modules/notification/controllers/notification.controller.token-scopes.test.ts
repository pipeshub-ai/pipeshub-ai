import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { Notifications } from '../../../../src/modules/notification/schema/notification.schema';
import {
  archiveNotification,
  deleteNotification,
  getNotificationStats,
  listNotifications,
  markAllRead,
  markRead,
  markUnread,
  unarchiveNotification,
} from '../../../../src/modules/notification/controllers/notification.controller';

// Every notification query a token can reach leaves chat notices out when the token lacks conversation:read, and
// none does for a session or a conversation:read token. Each per-id write is listed, so a route that loses the filter
// (and would let a kb:read token read a chat notice back through its answer) fails here.
const HIDDEN = { $not: /^chat\./ };
const ids = () => ({ userId: new mongoose.Types.ObjectId().toString(), orgId: new mongoose.Types.ObjectId().toString() });

const tokens: [string, Record<string, unknown>, boolean][] = [
  ['kb:read PAT', { isOAuth: true, oauthScopes: ['kb:read'] }, true],
  ['conversation:write only', { isOAuth: true, oauthScopes: ['conversation:write'] }, true],
  ['conversation:chat only', { isOAuth: true, oauthScopes: ['conversation:chat'] }, true],
  ['empty scope list', { isOAuth: true, oauthScopes: [] }, true],
  ['missing scope list', { isOAuth: true }, true],
  ['scopes as a string, not a list', { isOAuth: true, oauthScopes: 'conversation:read' }, true],
  ['conversation:read', { isOAuth: true, oauthScopes: ['conversation:read'] }, false],
  ['browser session', {}, false],
];

const perId = { markRead, markUnread, archiveNotification, unarchiveNotification, deleteNotification };

describe('notification controller: the token-scope filter on every query', () => {
  afterEach(() => sinon.restore());

  const res = () => ({ status: sinon.stub().returnsThis(), json: sinon.stub() });

  for (const [tokenName, token, hidden] of tokens) {
    for (const [name, handler] of Object.entries(perId)) {
      it(`${name}: ${tokenName} ${hidden ? 'cannot' : 'can'} reach a chat notice`, async () => {
        const update = sinon.stub(Notifications, 'findOneAndUpdate').returns({ lean: sinon.stub().resolves(null) } as never);
        const next = sinon.stub();
        await (handler as (...a: unknown[]) => Promise<void>)(
          { user: { ...ids(), ...token }, params: { id: new mongoose.Types.ObjectId().toString() } },
          res(),
          next,
        );
        expect(next.called, String(next.firstCall?.args[0])).to.equal(false);
        const filter = update.firstCall.args[0] as Record<string, unknown>;
        if (hidden) expect(filter.type).to.deep.equal(HIDDEN);
        else expect(filter).to.not.have.property('type');
      });
    }

    it(`list, stats and read-all: ${tokenName} ${hidden ? 'skip' : 'include'} chat notices`, async () => {
      const find = sinon.stub(Notifications, 'find').returns({
        sort: sinon.stub().returnsThis(),
        limit: sinon.stub().returnsThis(),
        lean: sinon.stub().resolves([]),
      } as never);
      const count = sinon.stub(Notifications, 'countDocuments').resolves(0 as never);
      const many = sinon.stub(Notifications, 'updateMany').resolves({ modifiedCount: 0 } as never);
      const user = { ...ids(), ...token };
      await listNotifications()({ user, query: {} } as never, res() as never, sinon.stub());
      await getNotificationStats({ user } as never, res() as never, sinon.stub());
      await markAllRead({ user } as never, res() as never, sinon.stub());
      const filters = [find.firstCall.args[0], ...count.getCalls().map((c) => c.args[0]), many.firstCall.args[0]] as Record<string, unknown>[];
      expect(filters).to.have.length(5);
      for (const f of filters) {
        if (hidden) expect(f.type).to.deep.equal(HIDDEN);
        else expect(f).to.not.have.property('type');
      }
    });
  }
});
