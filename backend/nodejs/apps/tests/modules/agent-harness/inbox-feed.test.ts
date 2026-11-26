/**
 * G2 (ADR-INBOX-01): one Inbox per person. The feed merges the asks waiting on them (approvals, questions, skill
 * proposals, memory reviews) with their notices, asks first; filters, pages, counts (pause keeps the badge quiet,
 * never the asks), read/unread/archive/restore, mutes and the pause.
 */
import { expect } from 'chai';
import { InMemoryInboxStore } from '../../../src/modules/agent-harness/adapters/in-memory-inbox-store';
import { InboxFeedService, InboxScheduleNotFoundError } from '../../../src/modules/agent-harness/application/inbox-feed';
import {
  ApprovalWaitingSource,
  approvalItem,
  memoryItem,
  questionItem,
  skillItem,
  SkillProposalSource,
} from '../../../src/modules/agent-harness/application/inbox-sources';
import { decodeNoticeCursor, type InboxCounts, type InboxItem } from '../../../src/modules/agent-harness/domain/inbox';
import type { InboxViewer, InboxWaitingSource } from '../../../src/modules/agent-harness/ports/inbox';
import { RepositoryInboxNotices } from '../../../src/libs/repositories/agent-harness/inbox/notices';
import { FakeNotifications } from './support/fake-notifications';

const ANA: InboxViewer = { orgId: 'o1', userId: 'ana', isAdmin: false, authorization: 'Bearer ana' };
const BEN: InboxViewer = { orgId: 'o1', userId: 'ben', isAdmin: false };
const NOW = 1_700_000_000_000;

const approval = approvalItem({
  runId: 'r1',
  sessionId: 'c1',
  actorId: 'ana',
  runStatus: 'waiting_approval',
  runUpdatedAt: NOW - 5_000,
  interactionId: 'i1',
  toolName: 'jira.create_issue',
  displayName: 'Create a Jira issue',
  argsHidden: false,
  argsSummary: 'OPS: weekly report',
  authority: { approve: true, deny: true },
} as never);

const source = (name: InboxWaitingSource['name'], items: InboxItem[] | Error): InboxWaitingSource & { asked: InboxViewer[] } => {
  const asked: InboxViewer[] = [];
  return {
    name,
    asked,
    waiting: async (viewer) => {
      asked.push(viewer);
      if (items instanceof Error) throw items;
      return items;
    },
  };
};

function world(waiting: InboxWaitingSource[] = []) {
  const repo = new FakeNotifications();
  const store = new InMemoryInboxStore();
  const pushed: Array<[string, InboxCounts]> = [];
  const feed = new InboxFeedService({
    notices: new RepositoryInboxNotices(() => repo),
    waiting,
    preferences: store,
    schedules: async (_o, userId, _a, scheduleId) => userId === 'ana' && scheduleId === 's-ana',
    publisher: { publish: (userId, counts) => pushed.push([userId, counts]) },
    now: () => NOW,
  });
  return { repo, store, feed, pushed };
}

describe('G2 the Inbox feed (ADR-INBOX-01)', () => {
  it('IBX-F1: asks come first, then notices newest first; every item has a stable kind, severity, title, time and pointer', async () => {
    const { repo, feed } = world([source('approvals', [approval]), source('questions', [])]);
    repo.seed(ANA, { createdAt: new Date(NOW - 1_000), type: 'AGENT_RUN_FINISHED', title: 'Report', payload: { conversationId: 'c9' } });
    repo.seed(ANA, { createdAt: new Date(NOW - 100), type: 'chatShared', title: 'Jordan shared a chat with you' });
    const page = await feed.feed(ANA, { view: 'inbox', filter: 'all', limit: 30 });
    expect(page.items.map((i) => [i.kind, i.waiting])).to.deep.equal([
      ['approval', true],
      ['share', false],
      ['run_finished', false],
    ]);
    expect(page.items[0]).to.deep.include({
      id: 'a:r1:i1',
      severity: 'warning',
      title: 'Approve: Create a Jira issue',
      description: 'OPS: weekly report',
      at: NOW - 5_000,
      link: '/chat/?conversationId=c1',
    });
    expect(page.items[0]!.pointer).to.deep.equal({ type: 'conversation', conversationId: 'c1', runId: 'r1', interactionId: 'i1' });
    expect(page.counts).to.deep.equal({ unread: 2, waiting: 1, badge: 3, paused: false, pausedUntil: null });
  });

  it("IBX-F2: a person's feed holds only their own notices", async () => {
    const { repo, feed } = world();
    repo.seed(BEN, { createdAt: new Date(NOW), title: 'Ben only' });
    repo.seed({ orgId: 'o2', userId: 'ana' }, { createdAt: new Date(NOW), title: 'Other org' });
    expect((await feed.feed(ANA, { view: 'inbox', filter: 'all', limit: 30 })).items).to.deep.equal([]);
  });

  it('IBX-F3: a source that cannot answer is named in partial and nothing of it is guessed; the rest still list', async () => {
    const { feed } = world([source('approvals', [approval]), source('skills', new Error('down'))]);
    const page = await feed.feed(ANA, { view: 'inbox', filter: 'all', limit: 30 });
    expect(page.partial).to.deep.equal(['skills']);
    expect(page.items.map((i) => i.id)).to.deep.equal(['a:r1:i1']);
  });

  it('IBX-F4: filters list their kinds; unread lists every ask and unread notices only', async () => {
    const memory = memoryItem({ memoryId: 'm1', content: 'Prefers tables', createdAt: NOW - 50, agentKey: null } as never, true);
    const { repo, feed } = world([source('approvals', [approval]), source('memories', [memory])]);
    repo.seed(ANA, { createdAt: new Date(NOW - 10), type: 'AGENT_SCHEDULE_RUN_ENDED', payload: { status: 'completed', scheduleId: 's-ana' } });
    repo.seed(ANA, { createdAt: new Date(NOW - 20), type: 'chatShared', status: 'read' });
    repo.seed(ANA, { createdAt: new Date(NOW - 30), type: 'SYNC', originService: 'Connector Service' });
    const kinds = async (filter: Parameters<InboxFeedService['feed']>[1]['filter']) =>
      (await feed.feed(ANA, { view: 'inbox', filter, limit: 30 })).items.map((i) => i.kind);
    expect(await kinds('workflows')).to.deep.equal(['schedule_ran']);
    expect(await kinds('shared')).to.deep.equal(['share']);
    expect(await kinds('connectors')).to.deep.equal(['connector']);
    expect(await kinds('skills')).to.deep.equal(['memory_review']);
    expect(await kinds('mentions')).to.deep.equal([]);
    expect(await kinds('unread')).to.deep.equal(['memory_review', 'approval', 'schedule_ran', 'connector']);
  });

  it('IBX-F5: notices page by cursor with asks on the first page only; the archived view lists archived notices', async () => {
    const { repo, feed } = world([source('approvals', [approval])]);
    for (let i = 0; i < 5; i += 1) repo.seed(ANA, { createdAt: new Date(NOW - i * 10), title: `n${i}` });
    repo.seed(ANA, { createdAt: new Date(NOW - 100), title: 'old', status: 'archived' });
    const first = await feed.feed(ANA, { view: 'inbox', filter: 'all', limit: 2 });
    expect(first.items.map((i) => i.title)).to.deep.equal(['Approve: Create a Jira issue', 'n0', 'n1']);
    const second = await feed.feed(ANA, { view: 'inbox', filter: 'all', limit: 2, cursor: decodeNoticeCursor(first.cursor!)! });
    expect(second.items.map((i) => i.title)).to.deep.equal(['n2', 'n3']);
    const last = await feed.feed(ANA, { view: 'inbox', filter: 'all', limit: 2, cursor: decodeNoticeCursor(second.cursor!)! });
    expect(last.items.map((i) => i.title)).to.deep.equal(['n4']);
    expect(last.cursor).to.equal(undefined);
    const archived = await feed.feed(ANA, { view: 'archived', filter: 'all', limit: 30 });
    expect(archived.items.map((i) => [i.title, i.archived])).to.deep.equal([['old', true]]);
  });

  it('IBX-F6: a sparse filter reads forward a bounded number of pages and hands back a cursor to continue', async () => {
    const { repo, feed } = world();
    repo.seed(ANA, { createdAt: new Date(NOW - 1), type: 'chatShared', title: 'first share' });
    for (let i = 0; i < 600; i += 1) repo.seed(ANA, { createdAt: new Date(NOW - 10 - i), title: `noise ${i}` });
    repo.seed(ANA, { createdAt: new Date(NOW - 5_000), type: 'chatShared', title: 'old share' });
    const page = await feed.feed(ANA, { view: 'inbox', filter: 'shared', limit: 30 });
    expect(page.items.map((i) => i.title)).to.deep.equal(['first share']);
    expect(page.cursor).to.be.a('string');
    const next = await feed.feed(ANA, { view: 'inbox', filter: 'shared', limit: 30, cursor: decodeNoticeCursor(page.cursor!)! });
    expect(next.items.map((i) => i.title)).to.deep.equal(['old share']);
  });

  it("IBX-F7: read, unread, archive, unarchive and restore change only the caller's notices and push the new counts", async () => {
    const { repo, feed, pushed } = world();
    const a = repo.seed(ANA, { createdAt: new Date(NOW - 1) });
    const b = repo.seed(ANA, { createdAt: new Date(NOW - 2) });
    const bens = repo.seed(BEN, { createdAt: new Date(NOW - 3) });
    const read = await feed.apply(ANA, 'read', { ids: [a._id, bens._id] });
    expect(read.changed).to.deep.equal([`n:${a._id}`]);
    expect(bens.status).to.equal('unread');
    expect(read.counts.unread).to.equal(1);
    expect(pushed.map(([user]) => user)).to.deep.equal(['ana']);
    expect((await feed.apply(ANA, 'archive', { all: true })).changed).to.deep.equal([`n:${a._id}`]);
    expect((await feed.apply(ANA, 'read', { all: true })).changed).to.deep.equal([`n:${b._id}`]);
    expect((await feed.apply(ANA, 'unread', { ids: [b._id] })).changed).to.deep.equal([`n:${b._id}`]);
    expect((await feed.apply(ANA, 'restore', { all: true })).changed).to.deep.equal([`n:${a._id}`]);
    expect(a.status).to.equal('read');
    const nothing = await feed.apply(ANA, 'archive', { ids: ['not-an-id-of-any-store'] });
    expect(nothing.changed).to.deep.equal([]);
    expect(pushed).to.have.length(5);
  });

  it("IBX-F8: opening a conversation reads its finished-run notices and leaves everything else", async () => {
    const { repo, feed } = world();
    const done = repo.seed(ANA, { createdAt: new Date(NOW - 1), type: 'AGENT_RUN_FINISHED', payload: { conversationId: 'c1' } });
    const other = repo.seed(ANA, { createdAt: new Date(NOW - 2), type: 'AGENT_RUN_FINISHED', payload: { conversationId: 'c2' } });
    const share = repo.seed(ANA, { createdAt: new Date(NOW - 3), type: 'chatShared', payload: { conversationId: 'c1' } });
    expect((await feed.apply(ANA, 'read', { conversationId: 'c1' })).changed).to.deep.equal([`n:${done._id}`]);
    expect([other.status, share.status]).to.deep.equal(['unread', 'unread']);
  });

  it('IBX-F9: a pause quiets the badge but never the asks, and tells open sessions at once', async () => {
    const { repo, feed, pushed } = world([source('questions', [questionItem({ runId: 'r', sessionId: 'c', runUpdatedAt: NOW, callId: 'k', question: 'Which repo?', options: [], multiSelect: false, moreQuestions: 0 })])]);
    repo.seed(ANA, { createdAt: new Date(NOW - 1) });
    const prefs = await feed.updatePreferences(ANA, { pause: { choice: 'hour' } });
    expect(prefs.pause).to.deep.equal({ until: NOW + 3_600_000 });
    expect(pushed.at(-1)).to.deep.equal(['ana', { unread: 1, waiting: 1, badge: 1, paused: true, pausedUntil: NOW + 3_600_000 }]);
    expect((await feed.counts(ANA)).badge).to.equal(1);
    await feed.updatePreferences(ANA, { pause: { choice: 'off' } });
    expect((await feed.counts(ANA)).badge).to.equal(2);
  });

  it('IBX-F10: a person mutes only a workflow they receive notices of; unmuting needs no lookup', async () => {
    const { feed } = world();
    expect((await feed.updatePreferences(ANA, { mute: { scheduleId: 's-ana', muted: true } })).mutedSchedules).to.deep.equal(['s-ana']);
    let refused: unknown;
    try {
      await feed.updatePreferences(BEN, { mute: { scheduleId: 's-ana', muted: true } });
    } catch (error) {
      refused = error;
    }
    expect(refused).to.be.instanceOf(InboxScheduleNotFoundError);
    expect((await feed.updatePreferences(ANA, { mute: { scheduleId: 's-ana', muted: false } })).mutedSchedules).to.deep.equal([]);
    const channels = await feed.updatePreferences(ANA, { runNotices: { email: true } });
    expect(channels.runNotices).to.deep.equal({ email: true, slack: false });
  });

  it("IBX-F11: skill proposals wait on whoever may decide them (the learner, an admin), asked with their own credential", async () => {
    const asked: string[] = [];
    const skills = new SkillProposalSource({
      pending: async (authorization) => {
        asked.push(authorization);
        return [{ candidateId: 'k1', name: 'Triage tickets', description: 'Sort new tickets by area', createdAt: NOW }];
      },
    });
    // UI-B4: the skills service decides who sees which (a member their own, an admin all); without a credential, none.
    expect(await skills.waiting({ ...ANA, authorization: undefined }, 50)).to.deep.equal([]);
    expect(skills.credentialOnly).to.equal(true);
    const items = await skills.waiting(ANA, 50);
    expect(items).to.deep.equal([
      skillItem({ candidateId: 'k1', name: 'Triage tickets', description: 'Sort new tickets by area', createdAt: NOW }),
    ]);
    expect(items[0]).to.deep.include({ kind: 'skill_proposal', waiting: true, title: 'New skill proposed: Triage tickets' });
    expect(items[0]?.subject).to.deep.equal({ skill: 'Triage tickets' });
    expect(asked).to.deep.equal(['Bearer ana']);
  });

  it('IBX-F13: filter=waiting (BG-1) lists the asks with the counts and never reads a notice page', async () => {
    const memory = memoryItem({ memoryId: 'm1', content: 'Prefers tables', createdAt: NOW - 50, agentKey: null } as never, true);
    const { repo, feed } = world([source('approvals', [approval]), source('memories', [memory])]);
    repo.seed(ANA, { createdAt: new Date(NOW - 10), type: 'AGENT_RUN_FINISHED', title: 'Report', payload: { conversationId: 'c9' } });
    repo.seed(ANA, { createdAt: new Date(NOW - 20), type: 'chatShared' });
    const pages: unknown[] = [];
    const list = repo.list.bind(repo);
    repo.list = async (...args) => {
      pages.push(args[1]);
      return list(...args);
    };
    const page = await feed.feed(ANA, { view: 'inbox', filter: 'waiting', limit: 30 });
    expect(page.items.map((i) => i.id)).to.deep.equal(['m:m1', 'a:r1:i1']);
    expect(page.cursor).to.equal(undefined);
    expect(page.counts).to.deep.equal({ unread: 2, waiting: 2, badge: 4, paused: false, pausedUntil: null });
    expect(pages).to.deep.equal([]);
    const archived = await feed.feed(ANA, { view: 'archived', filter: 'waiting', limit: 30 });
    expect(archived.items).to.deep.equal([]);
    expect(pages).to.deep.equal([]);
  });

  it("IBX-F12: the badge counts approvals without recording a read of anyone's arguments; the feed that shows them records it", async () => {
    const asked: Array<boolean | undefined> = [];
    const queue = new ApprovalWaitingSource({
      pending: async (query) => {
        asked.push(query.audited);
        return [];
      },
    });
    const { feed } = world([queue]);
    await feed.counts(ANA);
    await feed.feed(ANA, { view: 'inbox', filter: 'shared', limit: 30 });
    await feed.feed(ANA, { view: 'inbox', filter: 'all', limit: 30 });
    expect(asked).to.deep.equal([false, false, true]);
  });
});
