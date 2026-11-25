/**
 * UI-B4 for the Inbox (ADR-INBOX-01): every kind carries its typed subject (UIP3-FU-1), archived notices are deleted
 * one, several or all at once and nothing else is (UIP3-FU-2), and a push that could not count what only the person's
 * own credential counts says so (FIXMS1-FU-1).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { RepositoryInboxNotices } from '../../../src/libs/repositories/agent-harness/inbox/notices';
import { InMemoryInboxStore } from '../../../src/modules/agent-harness/adapters/in-memory-inbox-store';
import { InboxFeedService } from '../../../src/modules/agent-harness/application/inbox-feed';
import {
  approvalItem,
  memoryItem,
  questionItem,
  skillItem,
  SkillProposalSource,
  toolApp,
} from '../../../src/modules/agent-harness/application/inbox-sources';
import { inboxItemSchema } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/inbox-item';
import { inboxCountsSchema } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/inbox-counts';
import {
  INBOX_ITEM_KINDS,
  INBOX_SUBJECT_VERSION,
  noticeItem,
  type InboxCounts,
  type InboxItem,
  type InboxItemKind,
  type InboxSubject,
} from '../../../src/modules/agent-harness/domain/inbox';
import type { MemoryView } from '../../../src/modules/agent-harness/domain/memory';
import type { NotificationRow } from '../../../src/libs/repositories/notification/types';
import { FakeNotifications } from './support/fake-notifications';

const ORG = '6650a1b2c3d4e5f607182930';
const ANA = '6650a1b2c3d4e5f607182940';
const BEN = '6650a1b2c3d4e5f607182941';
const NONE: ReadonlySet<string> = new Set();

const row = (over: Partial<NotificationRow>): NotificationRow =>
  ({
    _id: 'nid1',
    orgId: ORG,
    assignedTo: ANA,
    status: 'unread',
    title: 'English title',
    message: 'English words',
    createdAt: new Date(1_000),
    ...over,
  }) as NotificationRow;

const approval = (over: Record<string, unknown> = {}) =>
  ({
    runId: 'r1',
    sessionId: 'c1',
    interactionId: 'ix1',
    toolName: 'jira__create_issue',
    displayName: 'Create issue',
    requestedAt: 1_000,
    runUpdatedAt: 1_000,
    authority: { approve: true, deny: true },
    ...over,
  }) as never;

/** One item of each kind, as its source or its writer's payload makes it, and the subject it must carry. */
const CASES: Record<InboxItemKind, Array<[InboxItem, InboxSubject]>> = {
  approval: [
    [approvalItem(approval()), { action: { name: 'Create issue', app: 'jira', tool: 'jira__create_issue' } }],
    // CJFIX-A F4: an MCP tool's id and where it sends go with it, so the client words it as the chat's card does.
    [
      approvalItem(approval({ toolName: 'mcp_e2esaas_create_ticket', displayName: 'E2Esaas · create_ticket', reason: { rule: 'writes', sources: [], destination: '#sales' } })),
      { action: { name: 'E2Esaas · create_ticket', app: null, tool: 'mcp_e2esaas_create_ticket', destination: '#sales' } },
    ],
    [approvalItem(approval({ connect: { item: 'Google Drive' } })), { connect: 'Google Drive' }],
  ],
  question: [
    [
      questionItem({ runId: 'r1', sessionId: 'c1', runUpdatedAt: 1, callId: 't1', question: 'Which region?', options: [], multiSelect: false, moreQuestions: 0 }),
      { question: 'Which region?' },
    ],
  ],
  skill_proposal: [[skillItem({ candidateId: 'k1', name: 'Triage', description: 'd', createdAt: 1 }), { skill: 'Triage' }]],
  // A kind this build writes no item of; an edition's source does, with the skill as its subject.
  artefact_review: [
    [
      {
        id: 'r:skill:pdf-tools',
        kind: 'artefact_review',
        severity: 'info',
        waiting: true,
        unread: true,
        archived: false,
        title: 'pdf-tools',
        description: 'd',
        at: 1,
        pointer: { type: 'catalog_item', itemKind: 'skill', name: 'pdf-tools' },
        link: '/workspace/skills/personal/',
        subjectVersion: INBOX_SUBJECT_VERSION,
        subject: { skill: 'pdf-tools' },
      } as InboxItem,
      { skill: 'pdf-tools' },
    ],
  ],
  memory_review: [
    [memoryItem({ memoryId: 'm1', content: 'Ana prefers CSV', agentKey: null, createdAt: 1, status: 'proposed' } as MemoryView, true), { memory: 'Ana prefers CSV' }],
  ],
  run_finished: [
    [noticeItem(row({ type: 'AGENT_RUN_FINISHED', payload: { conversationId: 'c1', chatTitle: 'Q3 report', finished: true } }), NONE), { chat: 'Q3 report', outcome: 'finished' }],
    [noticeItem(row({ type: 'AGENT_RUN_FINISHED', payload: { conversationId: 'c1', chatTitle: 'Q3 report', cutShort: true } }), NONE), { chat: 'Q3 report', outcome: 'cut_short' }],
  ],
  run_not_finished: [
    [noticeItem(row({ type: 'AGENT_RUN_NOT_FINISHED', payload: { conversationId: 'c1', chatTitle: 'Q3 report' } }), NONE), { chat: 'Q3 report', outcome: 'not_finished' }],
  ],
  schedule_ran: [
    [noticeItem(row({ type: 'AGENT_SCHEDULE_RUN_ENDED', payload: { scheduleId: 's1', scheduleName: 'Monday digest', status: 'completed' } }), NONE), { workflow: 'Monday digest', outcome: 'ran' }],
  ],
  schedule_failed: [
    [noticeItem(row({ type: 'AGENT_SCHEDULE_RUN_ENDED', payload: { scheduleId: 's1', scheduleName: 'Monday digest', status: 'failed' } }), NONE), { workflow: 'Monday digest', outcome: 'failed' }],
    [
      noticeItem(row({ type: 'AGENT_SCHEDULE_FIRING_REFUSED', payload: { scheduleId: 's1', scheduleName: 'Monday digest', reason: 'agent_unavailable' } }), NONE),
      // UIADOPT2-FU-2: a code outside the closed set reads unavailable.
      { workflow: 'Monday digest', outcome: 'refused', reason: 'unavailable' },
    ],
  ],
  schedule_waiting: [
    [
      noticeItem(row({ type: 'AGENT_SCHEDULE_APPROVAL_WAITING', payload: { scheduleId: 's1', scheduleName: 'Monday digest', deadlineAt: 9_000 } }), NONE),
      { workflow: 'Monday digest', outcome: 'waiting', deadlineAt: 9_000 },
    ],
  ],
  share: [
    [noticeItem(row({ type: 'chatShared', payload: { conversationId: 'c1', sharerName: 'Ben Ortiz', chatTitle: 'Q3 report' } }), NONE), { sharedBy: 'Ben Ortiz', chat: 'Q3 report' }],
  ],
  mention: [[noticeItem(row({ type: 'chatMention', payload: { conversationId: 'c1', chatTitle: 'Q3 report' } }), NONE), { chat: 'Q3 report' }]],
  connector: [[noticeItem(row({ type: 'SYNC_DONE', originService: 'Connector Service' }), NONE), { source: 'Connector Service' }]],
  system: [[noticeItem(row({ type: 'SOMETHING', originService: 'Platform' }), NONE), { source: 'Platform' }]],
};

describe('UI-B4 the Inbox: typed subjects, deleting archived notices, the recount hint', () => {
  it('IBX-S1: every kind carries its subject (version 1) and answers the versioned contract', () => {
    expect(Object.keys(CASES).sort()).to.deep.equal([...INBOX_ITEM_KINDS].sort());
    for (const [kind, cases] of Object.entries(CASES)) {
      for (const [item, subject] of cases) {
        expect(item.kind, kind).to.equal(kind);
        expect(item.subjectVersion, kind).to.equal(1);
        expect(item.subject, kind).to.deep.equal(subject);
        // The English title stays as the fallback for a client that knows no subject.
        expect(item.title.length, kind).to.be.greaterThan(0);
        const parsed = inboxItemSchema.safeParse(item);
        expect(parsed.success, `${kind}: ${JSON.stringify(parsed.error?.issues ?? [])}`).to.equal(true);
      }
    }
  });

  it("IBX-S2: a subject is read from the writer's payload, never its sentences; a notice written before has none of the names", () => {
    const old = noticeItem(row({ type: 'AGENT_SCHEDULE_RUN_ENDED', title: 'A scheduled run finished', message: '"Monday digest": done', payload: { scheduleId: 's1', status: 'completed' } }), NONE);
    expect(old.subject).to.deep.equal({ outcome: 'ran' });
    expect(inboxItemSchema.safeParse(old).success).to.equal(true);
    const long = noticeItem(row({ type: 'chatShared', payload: { sharerName: '  '.concat('x'.repeat(400)), chatTitle: '' } }), NONE);
    expect(Array.from(long.subject.sharedBy ?? '').length).to.equal(256);
    expect(long.subject.chat).to.equal(undefined);
    expect([toolApp('mcp.github.list_prs'), toolApp('jira.search'), toolApp('fetch_url')]).to.deep.equal(['github', 'jira', null]);
  });

  it('IBX-D1: deleting archived notices takes only archived ones of the caller, by id or all', async () => {
    const repo = new FakeNotifications();
    const notices = new RepositoryInboxNotices(() => repo);
    const ana = { orgId: ORG, userId: ANA };
    const a1 = repo.seed(ana, { createdAt: new Date(3_000), status: 'archived' });
    const a2 = repo.seed(ana, { createdAt: new Date(2_000), status: 'archived' });
    const unread = repo.seed(ana, { createdAt: new Date(1_000), status: 'unread' });
    const bens = repo.seed({ orgId: ORG, userId: BEN }, { createdAt: new Date(4_000), status: 'archived' });

    expect(await notices.removeArchived(ana, { ids: [a1._id, unread._id, bens._id, 'nope'] })).to.deep.equal([a1._id]);
    expect(await notices.removeArchived(ana, { ids: [] })).to.deep.equal([]);
    expect(await notices.removeArchived(ana, { all: true })).to.deep.equal([a2._id]);
    expect((await repo.counts(ana)).unread).to.equal(1);
    expect((await repo.counts({ orgId: ORG, userId: BEN })).archived).to.equal(1);
  });

  it('IBX-D2: the service answers the deleted item ids and the counts, and pushes them only when something went', async () => {
    const repo = new FakeNotifications();
    const pushed: InboxCounts[] = [];
    const feed = new InboxFeedService({
      notices: new RepositoryInboxNotices(() => repo),
      waiting: [],
      preferences: new InMemoryInboxStore(),
      schedules: async () => false,
      publisher: { publish: (_user, counts) => pushed.push(counts) },
    });
    const viewer = { orgId: ORG, userId: ANA, isAdmin: false };
    const archived = repo.seed(viewer, { createdAt: new Date(1_000), status: 'archived' });
    expect(await feed.removeArchived(viewer, { all: true })).to.deep.include({ changed: [`n:${archived._id}`] });
    expect(pushed).to.have.length(1);
    expect((await feed.removeArchived(viewer, { all: true })).changed).to.deep.equal([]);
    expect(pushed).to.have.length(1);
  });

  it("IBX-RC1: a push carries recount when a source counts only with the person's credential; a read never does", async () => {
    const pushed: InboxCounts[] = [];
    const make = (withSkills: boolean) =>
      new InboxFeedService({
        notices: { unreadCount: async () => 2 } as never,
        waiting: withSkills ? [new SkillProposalSource({ pending: async () => [{ candidateId: 'k1', name: 'T', description: '', createdAt: 1 }] })] : [],
        preferences: new InMemoryInboxStore(),
        schedules: async () => false,
        publisher: { publish: (_user, counts) => pushed.push(counts) },
      });
    await make(true).announce(ORG, ANA);
    await make(false).announce(ORG, ANA);
    expect(pushed.map((c) => [c.waiting, c.recount])).to.deep.equal([
      [0, true],
      [0, undefined],
    ]);
    expect(inboxCountsSchema.safeParse(pushed[0]).success).to.equal(true);
    const read = await make(true).counts({ orgId: ORG, userId: ANA, isAdmin: false, authorization: 'Bearer ana' });
    expect([read.waiting, read.recount]).to.deep.equal([1, undefined]);
  });
});
