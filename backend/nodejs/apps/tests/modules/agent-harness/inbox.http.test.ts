/**
 * G2 (ADR-INBOX-01): the Inbox routes as the app mounts them. The caller is the verified token's
 * person, never a body or query field; every answer is its published contract; a bad filter, cursor or selection is
 * a 400; a mute of a schedule the caller does not receive notices of is a 404.
 */
import 'reflect-metadata';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { expect } from 'chai';
import express from 'express';
import { Container } from 'inversify';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import type { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import { RepositoryInboxNotices } from '../../../src/libs/repositories/agent-harness/inbox/notices';
import { InMemoryInboxStore } from '../../../src/modules/agent-harness/adapters/in-memory-inbox-store';
import { InboxFeedService } from '../../../src/modules/agent-harness/application/inbox-feed';
import {
  AGENT_HARNESS_INBOX,
  AGENT_HARNESS_ORG_ADMIN_CHECK,
} from '../../../src/modules/agent-harness/binders/tokens';
import { INBOX_PREFIX, createInboxRouter } from '../../../src/modules/agent-harness/transport/inbox-routes';
import type { InboxViewer } from '../../../src/modules/agent-harness/ports/inbox';
import { assertAnswersContract } from './support/api-contracts';
import { FakeNotifications } from './support/fake-notifications';

const ORG = '6650a1b2c3d4e5f607182930';
const ANA = '6650a1b2c3d4e5f607182940';
const BEN = '6650a1b2c3d4e5f607182941';

describe('G2 the Inbox routes (ADR-INBOX-01)', () => {
  let server: Server;
  let origin: string;
  let caller: Record<string, unknown> | undefined;
  const repo = new FakeNotifications();
  const askedBy: InboxViewer[] = [];

  before(() => {
    const container = new Container();
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({
      authenticate: (req: any, res: any, next: () => void) => {
        if (caller === undefined) {
          res.status(401).json({ error: { code: 'HTTP_UNAUTHORIZED' } });
          return;
        }
        req.user = { ...caller };
        next();
      },
    } as never);
    container.bind(AGENT_HARNESS_ORG_ADMIN_CHECK).toConstantValue(async (_org: string, userId: string) => userId === BEN);
    const store = new InMemoryInboxStore();
    container.bind(AGENT_HARNESS_INBOX).toConstantValue(
      new InboxFeedService({
        notices: new RepositoryInboxNotices(() => repo),
        waiting: [
          {
            name: 'approvals',
            waiting: async (viewer) => {
              askedBy.push(viewer);
              return [];
            },
          },
        ],
        preferences: store,
        schedules: async (_org, userId, _admin, scheduleId) => userId === ANA && scheduleId === 'sch-ana',
      }),
    );
    const app = express();
    app.use(express.json());
    app.use(INBOX_PREFIX, createInboxRouter(container));
    app.use(ErrorMiddleware.handleError());
    server = app.listen(0);
    origin = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
  });

  after(() => server.close());

  beforeEach(() => {
    caller = { userId: ANA, orgId: ORG };
    askedBy.length = 0;
  });

  const call = async (method: string, path: string, body?: unknown) => {
    const response = await fetch(`${origin}${path}`, {
      method,
      headers: { 'content-type': 'application/json', authorization: 'Bearer token-of-caller' },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    const json = (await response.json()) as Record<string, any>;
    assertAnswersContract(method, path, response.status, json);
    return { status: response.status, body: json };
  };

  it("IBX-H1: the feed is the token's person's, whatever the query says, and answers its contract", async () => {
    repo.seed({ orgId: ORG, userId: ANA }, { createdAt: new Date(2_000), type: 'AGENT_RUN_FINISHED', title: 'Report', payload: { conversationId: 'c1' } });
    repo.seed({ orgId: ORG, userId: BEN }, { createdAt: new Date(2_000), title: 'Ben only' });
    const { status, body } = await call('GET', `${INBOX_PREFIX}/?filter=all&limit=10`);
    expect(status).to.equal(200);
    expect(body.items.map((i: { title: string }) => i.title)).to.deep.equal(['Report']);
    expect(askedBy).to.deep.equal([{ orgId: ORG, userId: ANA, isAdmin: false, authorization: 'Bearer token-of-caller' }]);
    expect((await call('GET', `${INBOX_PREFIX}/?userId=${BEN}`)).status).to.equal(400);
  });

  it('IBX-H11: filter=waiting is accepted and answers the feed contract with no notice in it', async () => {
    repo.seed({ orgId: ORG, userId: ANA }, { createdAt: new Date(3_000), type: 'AGENT_RUN_FINISHED', title: 'Report', payload: { conversationId: 'c1' } });
    const { status, body } = await call('GET', `${INBOX_PREFIX}/?filter=waiting`);
    expect(status).to.equal(200);
    expect(body.items).to.deep.equal([]);
    expect(body.counts.unread).to.be.greaterThan(0);
    expect(askedBy).to.have.length(1);
  });

  it('IBX-H2: a bad filter, view or cursor is refused', async () => {
    expect((await call('GET', `${INBOX_PREFIX}/?filter=everything`)).status).to.equal(400);
    expect((await call('GET', `${INBOX_PREFIX}/?view=deleted`)).status).to.equal(400);
    expect((await call('GET', `${INBOX_PREFIX}/?cursor=not-a-cursor`)).status).to.equal(400);
    expect((await call('GET', `${INBOX_PREFIX}/?limit=500`)).status).to.equal(400);
  });

  it('IBX-H3: counts, notice actions and settings answer their contracts', async () => {
    const counts = await call('GET', `${INBOX_PREFIX}/count`);
    expect(counts.status).to.equal(200);
    expect(counts.body).to.have.keys(['unread', 'waiting', 'badge', 'paused', 'pausedUntil']);
    const read = await call('PATCH', `${INBOX_PREFIX}/notices`, { action: 'read', all: true });
    expect(read.status).to.equal(200);
    const prefs = await call('PATCH', `${INBOX_PREFIX}/preferences`, {
      pause: { choice: 'tomorrow', timeZone: 'Asia/Kolkata' },
      runNotices: { email: true },
    });
    expect(prefs.status).to.equal(200);
    expect(prefs.body.runNotices).to.deep.equal({ email: true, slack: false });
    expect(prefs.body.pause).to.have.key('until');
    expect((await call('GET', `${INBOX_PREFIX}/preferences`)).body).to.deep.equal(prefs.body);
    expect((await call('GET', `${INBOX_PREFIX}/count`)).body.paused).to.equal(true);
  });

  it('IBX-H4: a notice selection names exactly one of ids, all or a conversation, and only what the action allows', async () => {
    const bad = [
      { action: 'read' },
      { action: 'read', all: true, ids: ['n:abc'] },
      { action: 'unread', all: true },
      { action: 'archive', conversationId: 'c1' },
      { action: 'restore', ids: ['n:abc'] },
      { action: 'read', ids: ['a:r1:i1'] },
      { action: 'delete', all: true },
    ];
    for (const body of bad) expect((await call('PATCH', `${INBOX_PREFIX}/notices`, body)).status, JSON.stringify(body)).to.equal(400);
    expect((await call('PATCH', `${INBOX_PREFIX}/notices`, { action: 'read', conversationId: 'c1' })).status).to.equal(200);
  });

  it("IBX-H5: muting a schedule the caller does not receive notices of is not found; an unknown time zone is refused", async () => {
    expect((await call('PATCH', `${INBOX_PREFIX}/preferences`, { mute: { scheduleId: 'sch-ana', muted: true } })).status).to.equal(200);
    caller = { userId: BEN, orgId: ORG };
    expect((await call('PATCH', `${INBOX_PREFIX}/preferences`, { mute: { scheduleId: 'sch-ana', muted: true } })).status).to.equal(404);
    expect((await call('PATCH', `${INBOX_PREFIX}/preferences`, { pause: { choice: 'tomorrow', timeZone: 'Mars/Olympus' } })).status).to.equal(400);
    expect((await call('PATCH', `${INBOX_PREFIX}/preferences`, {})).status).to.equal(400);
  });

  it("IBX-H9 (UIP3-FU-2): deleting archived notices is the token's person's, by ids or all, and answers its contract", async () => {
    const mine = repo.seed({ orgId: ORG, userId: ANA }, { createdAt: new Date(7_000), status: 'archived', title: 'Old' });
    const theirs = repo.seed({ orgId: ORG, userId: BEN }, { createdAt: new Date(7_001), status: 'archived', title: 'Ben' });
    const one = await call('POST', `${INBOX_PREFIX}/notices/delete`, { ids: [`n:${theirs._id}`, `n:${mine._id}`] });
    expect(one.status).to.equal(200);
    expect(one.body['changed']).to.deep.equal([`n:${mine._id}`]);
    const all = await call('POST', `${INBOX_PREFIX}/notices/delete`, { all: true });
    expect(all.status).to.equal(200);
    expect((await repo.counts({ orgId: ORG, userId: BEN })).archived).to.be.greaterThan(0);
  });

  it('IBX-H10: a delete names ids or all, never both or neither, nor another field', async () => {
    for (const body of [{}, { all: true, ids: ['n:abc'] }, { ids: [] }, { all: false }, { all: true, userId: BEN }, { ids: ['a:r1:ix'] }]) {
      expect((await call('POST', `${INBOX_PREFIX}/notices/delete`, body)).status, JSON.stringify(body)).to.equal(400);
    }
  });

  it('IBX-H6: no token, no Inbox', async () => {
    caller = undefined;
    expect((await call('GET', `${INBOX_PREFIX}/`)).status).to.equal(401);
  });
});
