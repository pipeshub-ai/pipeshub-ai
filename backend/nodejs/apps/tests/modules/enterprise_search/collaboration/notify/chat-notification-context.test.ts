/// <reference types="mocha" />
import 'reflect-metadata';
import express from 'express';
import type { Server } from 'http';
import { expect } from 'chai';
import sinon from 'sinon';
import { Types } from 'mongoose';
import {
  CHAT_TITLE_MAX_CHARS,
  ChatNotificationContext,
  ContextRow,
  SessionBatchLoader,
  SessionWithTitle,
  loadSessionsForNotifications,
} from '../../../../../src/modules/enterprise_search/services/collaboration/notify/chat-notification-context';
import { ChatSession } from '../../../../../src/modules/enterprise_search/schema/chat.session.schema';
import { Notifications } from '../../../../../src/modules/notification/schema/notification.schema';
import { listNotifications } from '../../../../../src/modules/notification/controllers/notification.controller';
import { AuthenticatedUserRequest } from '../../../../../src/libs/middlewares/types';

const oid = (): string => new Types.ObjectId().toString();

describe('collaboration/notify/chat-notification-context', () => {
  const orgId = oid();
  const userId = oid();
  const actorId = oid();
  const req = { user: { userId, orgId }, headers: {} } as unknown as AuthenticatedUserRequest;

  interface Seed {
    id: string;
    org?: string;
    title?: string;
    deleted?: boolean;
    readableBy?: string[];
  }
  let seeds: Seed[];
  let loadCalls: Array<{ orgId: string; ids: readonly string[] }>;
  let flagOn: boolean;
  let displayNames: sinon.SinonStub;
  let teamLookups: sinon.SinonStub;

  const loadSessions: SessionBatchLoader = async (org, ids) => {
    loadCalls.push({ orgId: org, ids });
    return seeds
      .filter((s) => (s.org ?? orgId) === org && !s.deleted && ids.includes(s.id))
      .map(
        (s) =>
          ({
            _id: new Types.ObjectId(s.id),
            title: s.title,
            readableBy: s.readableBy ?? [userId],
          }) as unknown as SessionWithTitle,
      );
  };

  const build = (): ChatNotificationContext =>
    new ChatNotificationContext({
      authz: {
        check: async (_subject, _action, resource, context) => {
          const session = context?.loaded?.session as unknown as {
            readableBy: string[];
          };
          expect(resource.type).to.equal('chat');
          return {
            allow: session.readableBy.includes(userId),
            role: 'viewer',
            via: [],
          };
        },
        explain: sinon.stub(),
      } as never,
      teams: { callerTeamIds: teamLookups } as never,
      users: { displayNames } as never,
      flags: { isEnabled: async () => flagOn } as never,
      logger: { warn: sinon.stub() },
      loadSessions,
    });

  const row = (
    type: string,
    sessionId: string,
    extra: Record<string, unknown> = {},
  ): ContextRow => ({
    type,
    payload: { sessionId, actorUserId: actorId, ...extra },
  });

  beforeEach(() => {
    seeds = [];
    loadCalls = [];
    flagOn = true;
    displayNames = sinon.stub().resolves(new Map([[actorId, 'Ada Lovelace']]));
    teamLookups = sinon.stub().resolves({ status: 'ok', teamIds: [] });
  });

  it('returns chat title and actor name for a readable chat', async () => {
    const id = oid();
    seeds = [{ id, title: 'Quarterly plan' }];
    const out = await build().resolve(req, [row('chat.shared', id)]);
    expect(out.get(0)).to.deep.equal({
      chatTitle: 'Quarterly plan',
      actorName: 'Ada Lovelace',
    });
    expect(displayNames.firstCall.args[2]).to.deep.equal({ emailFallback: false });
  });

  it('gives no context once read access is removed', async () => {
    const id = oid();
    seeds = [{ id, title: 'Secret', readableBy: [] }];
    const out = await build().resolve(req, [row('chat.shared', id)]);
    expect(out.size).to.equal(0);
    expect(displayNames.called).to.equal(false);
  });

  it('shows the new title after a rename', async () => {
    const id = oid();
    seeds = [{ id, title: 'Old' }];
    const ctx = build();
    expect((await ctx.resolve(req, [row('chat.shared', id)])).get(0)?.chatTitle).to.equal('Old');
    seeds[0].title = 'New';
    expect((await ctx.resolve(req, [row('chat.shared', id)])).get(0)?.chatTitle).to.equal('New');
  });

  it('gives no context for a deleted chat or a chat.deleted row', async () => {
    const gone = oid();
    const live = oid();
    seeds = [
      { id: gone, title: 'x', deleted: true },
      { id: live, title: 'y' },
    ];
    const out = await build().resolve(req, [
      row('chat.shared', gone),
      row('chat.deleted', live),
    ]);
    expect(out.size).to.equal(0);
  });

  it('never resolves a session id from another org', async () => {
    const id = oid();
    seeds = [{ id, title: 'Other org', org: oid() }];
    const out = await build().resolve(req, [row('chat.mentioned', id)]);
    expect(out.size).to.equal(0);
    expect(loadCalls[0].orgId).to.equal(orgId);
  });

  it('caps the title at 80 characters and skips non-chat rows', async () => {
    const id = oid();
    seeds = [{ id, title: 'é'.repeat(200) }];
    const out = await build().resolve(req, [
      row('chat.shared', id),
      { type: 'connector.error', payload: { sessionId: id } },
    ]);
    expect(Array.from(out.get(0)?.chatTitle ?? '')).to.have.length(CHAT_TITLE_MAX_CHARS);
    expect(out.has(1)).to.equal(false);
  });

  it('flag off does no lookups', async () => {
    flagOn = false;
    const id = oid();
    seeds = [{ id, title: 'T' }];
    const out = await build().resolve(req, [row('chat.shared', id)]);
    expect(out.size).to.equal(0);
    expect(loadCalls).to.have.length(0);
  });

  it('uses one session query and one name lookup per page', async () => {
    const ids = [oid(), oid(), oid()];
    seeds = ids.map((id) => ({ id, title: id }));
    const rows = [...ids, ids[0]].map((id) => row('chat.activity', id));
    const out = await build().resolve(req, rows);
    expect(out.size).to.equal(4);
    expect(loadCalls).to.have.length(1);
    expect(loadCalls[0].ids).to.have.length(3);
    expect(displayNames.callCount).to.equal(1);
    expect(teamLookups.callCount).to.equal(0);
  });

  it('a lookup failure leaves rows without context', async () => {
    const id = oid();
    seeds = [{ id, title: 'T' }];
    displayNames.rejects(new Error('boom'));
    const out = await build().resolve(req, [row('chat.shared', id)]);
    expect(out.size).to.equal(0);
  });

  it('default loader scopes by org and excludes deleted sessions in a single find', async () => {
    const exec = sinon.stub().resolves([]);
    const find = sinon.stub(ChatSession, 'find').returns({
      select: () => ({ lean: () => ({ exec }) }),
    } as never);
    const id = oid();
    await loadSessionsForNotifications(orgId, [id]);
    sinon.assert.calledOnce(find);
    const filter = find.firstCall.args[0] as Record<string, unknown>;
    expect(String(filter.orgId)).to.equal(orgId);
    expect(filter.isDeleted).to.equal(false);
    find.restore();
  });
});

describe('notification.controller listNotifications context', () => {
  let server: Server | undefined;
  const orgId = oid();
  const userId = oid();

  afterEach(async () => {
    sinon.restore();
    if (server) {
      await new Promise<void>((r) => server!.close(() => r()));
      server = undefined;
    }
  });

  async function get(
    context: Parameters<typeof listNotifications>[0],
  ): Promise<{ notifications: Array<Record<string, unknown>> }> {
    const stored = [
      { _id: oid(), type: 'chat.shared', payload: { sessionId: oid() } },
      { _id: oid(), type: 'connector.error' },
    ];
    sinon.stub(Notifications, 'find').returns({
      sort: sinon.stub().returnsThis(),
      limit: sinon.stub().returnsThis(),
      lean: sinon.stub().resolves(stored),
    } as never);
    const app = express();
    app.get(
      '/',
      (r, _s, n) => {
        (r as AuthenticatedUserRequest).user = { userId, orgId };
        n();
      },
      listNotifications(context),
    );
    const port = await new Promise<number>((resolve) => {
      server = app.listen(0, () =>
        resolve((server!.address() as { port: number }).port),
      );
    });
    return (await fetch(`http://127.0.0.1:${port}/`)).json();
  }

  it('adds context only to rows the service resolved', async () => {
    const body = await get({
      resolve: async () => new Map([[0, { chatTitle: 'T', actorName: 'A' }]]),
    });
    expect(body.notifications[0].context).to.deep.equal({ chatTitle: 'T', actorName: 'A' });
    expect('context' in body.notifications[1]).to.equal(false);
  });

  it('flag off (nothing resolved) leaves the response without a context field', async () => {
    const body = await get({ resolve: async () => new Map() });
    expect(body.notifications.some((n) => 'context' in n)).to.equal(false);
  });

  it('without a context service the response has no context field', async () => {
    const body = await get(undefined);
    expect(body.notifications.some((n) => 'context' in n)).to.equal(false);
  });
});
