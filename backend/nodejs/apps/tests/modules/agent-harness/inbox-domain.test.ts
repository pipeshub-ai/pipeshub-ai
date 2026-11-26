/**
 * G2 (ADR-INBOX-01): the Inbox's pure rules: what kind a notice is, which filter lists it, the order, the pause's
 * end on the person's own clock, the badge's counts, cursors, and which ended runs get a finished-run notice.
 */
import { expect } from 'chai';
import {
  CHAT_MENTION_NOTIFICATION,
  decodeNoticeCursor,
  encodeNoticeCursor,
  filterLists,
  filterListsNotices,
  filterListsWaiting,
  inboxCounts,
  INBOX_FILTERS,
  inboxOrder,
  noticeIdOf,
  noticeItem,
  noticeKind,
  pauseFor,
  WAITING_KINDS,
  runEndVerdict,
  runNoticeWords,
  RUN_FINISHED_NOTIFICATION,
  RUN_NOT_FINISHED_NOTIFICATION,
  safeLink,
  type InboxItem,
} from '../../../src/modules/agent-harness/domain/inbox';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import type { NotificationRow } from '../../../src/libs/repositories/notification/types';

const row = (over: Partial<NotificationRow> = {}): NotificationRow => ({
  _id: '65f1a2b3c4d5e6f708192a3b',
  orgId: 'o1',
  assignedTo: 'u1',
  type: 'AGENT_RUN_FINISHED',
  title: 'Quarterly report',
  message: 'Finished.',
  status: 'unread',
  isDeleted: false,
  createdAt: new Date(1_700_000_000_000),
  ...over,
});

const item = (over: Partial<InboxItem>): InboxItem => ({
  id: 'n:1',
  kind: 'system',
  severity: 'info',
  waiting: false,
  unread: false,
  archived: false,
  title: 't',
  description: '',
  at: 0,
  pointer: { type: 'link', path: '/' },
  link: '/',
  ...over,
});

describe('G2 Inbox domain (ADR-INBOX-01)', () => {
  it('IBX-D1: a notice is named by the type its writer gave it; connector news by its origin', () => {
    expect(noticeKind(row({ type: RUN_FINISHED_NOTIFICATION }))).to.equal('run_finished');
    expect(noticeKind(row({ type: RUN_NOT_FINISHED_NOTIFICATION }))).to.equal('run_not_finished');
    expect(noticeKind(row({ type: 'chatShared' }))).to.equal('share');
    expect(noticeKind(row({ type: CHAT_MENTION_NOTIFICATION }))).to.equal('mention');
    expect(noticeKind(row({ type: 'AGENT_SCHEDULE_RUN_ENDED', payload: { status: 'completed' } }))).to.equal('schedule_ran');
    expect(noticeKind(row({ type: 'AGENT_SCHEDULE_RUN_ENDED', payload: { status: 'failed' } }))).to.equal('schedule_failed');
    expect(noticeKind(row({ type: 'AGENT_SCHEDULE_FIRING_REFUSED' }))).to.equal('schedule_failed');
    expect(noticeKind(row({ type: 'AGENT_SCHEDULE_APPROVAL_WAITING' }))).to.equal('schedule_waiting');
    expect(noticeKind(row({ type: 'SYNC_DONE', originService: 'Connector Service' }))).to.equal('connector');
    expect(noticeKind(row({ type: 'X', originService: 'Indexing Service' }))).to.equal('connector');
    expect(noticeKind(row({ type: 'AGENT_ADMIN_ALERT', originService: 'AI Service' }))).to.equal('system');
  });

  it("IBX-D2: a notice item points at its schedule or conversation by the payload's ids, and says if it is muted", () => {
    const workflow = noticeItem(
      row({
        type: 'AGENT_SCHEDULE_RUN_ENDED',
        payload: { status: 'completed', scheduleId: 's1', runId: 'r1', conversationId: 'c1' },
        redirectLink: '/chat/?conversationId=c1',
      }),
      new Set(['s1']),
    );
    expect(workflow).to.deep.include({ id: 'n:65f1a2b3c4d5e6f708192a3b', kind: 'schedule_ran', severity: 'success', muted: true });
    expect(workflow.pointer).to.deep.equal({ type: 'schedule', scheduleId: 's1', runId: 'r1', conversationId: 'c1' });
    const finished = noticeItem(row({ payload: { conversationId: 'c2', agentKey: 'a1', runId: 'r2' } }), new Set());
    expect(finished.pointer).to.deep.equal({ type: 'conversation', conversationId: 'c2', agentKey: 'a1', runId: 'r2' });
    expect(finished).to.not.have.property('muted');
    expect(finished.at).to.equal(1_700_000_000_000);
    const other = noticeItem(row({ type: 'X', redirectLink: 'https://evil.example/' }), new Set());
    expect(other.pointer).to.deep.equal({ type: 'link', path: '/' });
    expect(other.link).to.equal('/');
  });

  it('IBX-D3: only an in-app path is ever a link', () => {
    expect(safeLink('/chat/?conversationId=c1')).to.equal('/chat/?conversationId=c1');
    expect(safeLink('//evil.example/')).to.equal('/');
    expect(safeLink('javascript:alert(1)')).to.equal('/');
    expect(safeLink(undefined)).to.equal('/');
  });

  it("IBX-D9: a notice's severity is its kind's; only a generic notice keeps the one stored, critical reading as error", () => {
    const sev = (type: string, severity?: string, payload?: Record<string, unknown>, originService?: string): string =>
      noticeItem(row({ type, ...(severity !== undefined ? { severity } : {}), ...(payload ? { payload } : {}), ...(originService ? { originService } : {}) }), new Set()).severity;
    expect(sev(RUN_FINISHED_NOTIFICATION)).to.equal('success');
    expect(sev(RUN_FINISHED_NOTIFICATION, 'warning')).to.equal('warning');
    expect(sev(RUN_FINISHED_NOTIFICATION, 'error')).to.equal('success');
    expect(sev(RUN_NOT_FINISHED_NOTIFICATION, 'info')).to.equal('error');
    expect(sev('AGENT_SCHEDULE_RUN_ENDED', 'warning', { status: 'completed' })).to.equal('warning');
    expect(sev('AGENT_SCHEDULE_RUN_ENDED', undefined, { status: 'failed' })).to.equal('error');
    expect(sev('AGENT_SCHEDULE_FIRING_REFUSED', 'warning')).to.equal('warning');
    expect(sev('AGENT_SCHEDULE_APPROVAL_WAITING', 'error')).to.equal('warning');
    expect(sev('chatShared', 'error')).to.equal('info');
    expect(sev(CHAT_MENTION_NOTIFICATION, 'warning')).to.equal('info');
    expect(sev('X', 'critical')).to.equal('error');
    expect(sev('X', 'success')).to.equal('success');
    expect(sev('SYNC', 'warning', undefined, 'Connector Service')).to.equal('warning');
    expect(sev('X', 'loud')).to.equal('info');
    expect(sev('X')).to.equal('info');
  });

  it('IBX-D10: a notice with no title is named by its kind; long titles and descriptions are clipped by code point', () => {
    const titled = (type: string, payload?: Record<string, unknown>): string =>
      noticeItem(row({ type, title: '   ', ...(payload ? { payload } : {}) }), new Set()).title;
    expect([
      titled(RUN_FINISHED_NOTIFICATION),
      titled(RUN_NOT_FINISHED_NOTIFICATION),
      titled('chatShared'),
      titled(CHAT_MENTION_NOTIFICATION),
      titled('AGENT_SCHEDULE_RUN_ENDED', { status: 'completed' }),
      titled('AGENT_SCHEDULE_FIRING_REFUSED'),
      titled('AGENT_SCHEDULE_APPROVAL_WAITING'),
      titled('X'),
    ]).to.deep.equal([
      'Finished',
      "Couldn't finish",
      'Shared with you',
      'You were mentioned',
      'A workflow ran',
      "A workflow couldn't run",
      'A workflow is waiting for you',
      'Notice',
    ]);
    expect(noticeItem(row({ title: undefined, message: undefined }), new Set())).to.deep.include({ title: 'Finished', description: '' });
    const long = noticeItem(row({ title: '😀'.repeat(400), message: `  ${'a'.repeat(1_200)}  ` }), new Set());
    expect(Array.from(long.title)).to.have.length(300);
    expect(long.title.endsWith('😀…')).to.equal(true);
    expect(long.description).to.have.length(1_000);
    expect(long.description.endsWith('a…')).to.equal(true);
    const exact = noticeItem(row({ title: 'b'.repeat(300) }), new Set());
    expect(exact.title).to.equal('b'.repeat(300));
  });

  it('IBX-D11: read and archived follow the stored status; a schedule id on a non-workflow notice points at its chat', () => {
    expect(noticeItem(row({ status: 'read' }), new Set())).to.deep.include({ unread: false, archived: false });
    expect(noticeItem(row({ status: 'archived' }), new Set())).to.deep.include({ unread: false, archived: true });
    expect(
      noticeItem(row({ payload: { scheduleId: 's1', conversationId: 'c1' } }), new Set(['s1'])),
    ).to.deep.include({ pointer: { type: 'conversation', conversationId: 'c1' } }).and.not.have.property('muted');
    const waiting = noticeItem(
      row({ type: 'AGENT_SCHEDULE_APPROVAL_WAITING', payload: { scheduleId: 's2', agentKey: 'a1', runId: '', conversationId: 7 } }),
      new Set(),
    );
    expect(waiting.pointer).to.deep.equal({ type: 'schedule', scheduleId: 's2', agentKey: 'a1' });
    expect(waiting.muted).to.equal(false);
    expect(
      noticeItem(row({ type: 'AGENT_SCHEDULE_FIRING_REFUSED', redirectLink: '/workflows/' }), new Set()).pointer,
    ).to.deep.equal({ type: 'link', path: '/workflows/' });
  });

  it('IBX-D4: each filter lists its kinds; unread lists every ask and unread notices', () => {
    const ask = item({ kind: 'approval', waiting: true, unread: true });
    const unread = item({ kind: 'run_finished', unread: true });
    const read = item({ kind: 'run_finished' });
    expect(filterLists('unread', ask)).to.equal(true);
    expect(filterLists('unread', unread)).to.equal(true);
    expect(filterLists('unread', read)).to.equal(false);
    expect(filterLists('all', read)).to.equal(true);
    expect(filterLists('workflows', item({ kind: 'schedule_failed' }))).to.equal(true);
    expect(filterLists('workflows', read)).to.equal(false);
    expect(filterLists('shared', item({ kind: 'share' }))).to.equal(true);
    expect(filterLists('mentions', item({ kind: 'mention' }))).to.equal(true);
    expect(filterLists('connectors', item({ kind: 'connector' }))).to.equal(true);
    expect(filterLists('skills', item({ kind: 'skill_proposal', waiting: true }))).to.equal(true);
    expect(filterLists('skills', item({ kind: 'memory_review', waiting: true }))).to.equal(true);
    expect(filterLists('skills', ask)).to.equal(false);
  });

  it('IBX-D12: waiting (Needs you, BG-1) lists every ask and no notice, and is the one filter beside skills that reads no notice source', () => {
    for (const kind of WAITING_KINDS) expect(filterLists('waiting', item({ kind, waiting: true }))).to.equal(true);
    expect(filterLists('waiting', item({ kind: 'run_finished', unread: true }))).to.equal(false);
    expect(filterLists('waiting', item({ kind: 'mention', unread: true }))).to.equal(false);
    expect(filterListsWaiting('waiting')).to.equal(true);
    expect(INBOX_FILTERS.filter((f) => !filterListsNotices(f))).to.deep.equal(['waiting', 'skills']);
    expect(INBOX_FILTERS.filter((f) => !filterListsWaiting(f))).to.deep.equal(['mentions', 'workflows', 'shared', 'connectors']);
  });

  it('IBX-D5: asks sort first, then newest first, ties by id', () => {
    const sorted = [
      item({ id: 'n:a', at: 30 }),
      item({ id: 'a:x', at: 10, waiting: true }),
      item({ id: 'n:c', at: 50 }),
      item({ id: 'q:y', at: 20, waiting: true }),
      item({ id: 'n:b', at: 50 }),
    ].sort(inboxOrder);
    expect(sorted.map((i) => i.id)).to.deep.equal(['q:y', 'a:x', 'n:b', 'n:c', 'n:a']);
  });

  it('IBX-D6: a pause ends in an hour, at 09:00 tomorrow on the person’s own clock, when resumed, or not at all', () => {
    const now = Date.UTC(2026, 10, 9, 22, 30);
    expect(pauseFor('hour', now)).to.deep.equal({ until: now + 3_600_000 });
    expect(pauseFor('tomorrow', now, 'UTC')).to.deep.equal({ until: Date.UTC(2026, 10, 10, 9) });
    // 22:30 UTC is 04:00 on the 10th in Kolkata: "tomorrow" there is the 11th, 09:00 IST (03:30 UTC).
    expect(pauseFor('tomorrow', now, 'Asia/Kolkata')).to.deep.equal({ until: Date.UTC(2026, 10, 11, 3, 30) });
    // 17:30 in New York (EST, UTC-5): tomorrow 09:00 EST is 14:00 UTC.
    expect(pauseFor('tomorrow', now, 'America/New_York')).to.deep.equal({ until: Date.UTC(2026, 10, 10, 14) });
    // Across a DST change: 13 March 2027 20:00 EST; 14 March 09:00 is EDT (UTC-4), 13:00 UTC.
    expect(pauseFor('tomorrow', Date.UTC(2027, 2, 14, 1), 'America/New_York')).to.deep.equal({
      until: Date.UTC(2027, 2, 14, 13),
    });
    expect(pauseFor('tomorrow', now, 'Not/AZone')).to.deep.equal({ until: Date.UTC(2026, 10, 10, 9) });
    expect(pauseFor('resumed', now)).to.deep.equal({ untilResumed: true });
    expect(pauseFor('off', now)).to.equal(null);
  });

  it('IBX-D7: the badge counts asks always and notices unless paused; a lapsed pause is no pause', () => {
    expect(inboxCounts(3, 2, { pause: null }, 10)).to.deep.equal({ unread: 3, waiting: 2, badge: 5, paused: false, pausedUntil: null });
    expect(inboxCounts(3, 2, { pause: { until: 20 } }, 10)).to.deep.equal({ unread: 3, waiting: 2, badge: 2, paused: true, pausedUntil: 20 });
    expect(inboxCounts(3, 2, { pause: { until: 20 } }, 20)).to.deep.include({ badge: 5, paused: false, pausedUntil: null });
    expect(inboxCounts(3, 0, { pause: { untilResumed: true } }, 10)).to.deep.equal({ unread: 3, waiting: 0, badge: 0, paused: true, pausedUntil: null });
  });

  it('IBX-D8: a notice cursor round-trips, and anything else is not a cursor', () => {
    const cursor = encodeNoticeCursor({ at: 1_700_000_000_000, id: '65f1a2b3c4d5e6f708192a3b' });
    expect(decodeNoticeCursor(cursor)).to.deep.equal({ at: 1_700_000_000_000, id: '65f1a2b3c4d5e6f708192a3b' });
    expect(decodeNoticeCursor('not-a-cursor')).to.equal(null);
    expect(decodeNoticeCursor(Buffer.from('[1,"$where"]').toString('base64url'))).to.equal(null);
    expect(noticeIdOf('n:abc')).to.equal('abc');
    expect(noticeIdOf('a:r1:i1')).to.equal(null);
  });

  describe('IBX-R: which ended runs leave a finished-run notice (ADR-INBOX-01 §Finished runs)', () => {
    const run = (over: Partial<HarnessRun>): HarnessRun =>
      ({ status: 'completed', outcome: 'answer', idempotencyKey: 'k1', input: {}, ...over }) as HarnessRun;

    it('IBX-R1: a person’s own interactive run that answered, failed or answered early is told', () => {
      expect(runEndVerdict(run({}))).to.deep.equal({ notify: true, finished: true, cutShort: false });
      expect(runEndVerdict(run({ truncatedBy: 'time' } as Partial<HarnessRun>))).to.deep.equal({
        notify: true,
        finished: true,
        cutShort: true,
      });
      expect(runEndVerdict(run({ status: 'failed', outcome: null }))).to.deep.equal({ notify: true, finished: false, cutShort: false });
    });

    it('IBX-R2: a job, a workflow firing, a service caller’s run, a cancelled run and a question are not', () => {
      expect(runEndVerdict(run({ parent: { runId: 'p1' } as never }))).to.deep.include({ reason: 'child' });
      expect(runEndVerdict(run({ idempotencyKey: 'schedule:s1:slot' }))).to.deep.include({ reason: 'scheduled' });
      expect(runEndVerdict(run({ input: { caller: { email: 'a@b.c' } } as never }))).to.deep.include({ reason: 'service_caller' });
      expect(runEndVerdict(run({ status: 'cancelled' }))).to.deep.include({ reason: 'cancelled' });
      expect(runEndVerdict(run({ outcome: 'needs_input' }))).to.deep.include({ reason: 'asked_a_question' });
      expect(runEndVerdict(run({ status: 'running' }))).to.deep.include({ reason: 'not_ended' });
    });

    it('IBX-R3: the notice is named by the chat and says how it went in plain words', () => {
      expect(runNoticeWords({ finished: true, cutShort: false, chatTitle: 'Q3 plan' })).to.deep.include({
        type: RUN_FINISHED_NOTIFICATION,
        severity: 'success',
        title: 'Q3 plan',
        outcome: 'Finished',
      });
      expect(runNoticeWords({ finished: true, cutShort: true, chatTitle: '' })).to.deep.include({ severity: 'warning', title: 'Your chat' });
      const failed = runNoticeWords({ finished: false, cutShort: false, chatTitle: 'x' });
      expect(failed).to.deep.include({ type: RUN_NOT_FINISHED_NOTIFICATION, severity: 'error', outcome: "Couldn't finish" });
      expect(failed.message).to.not.match(/run|id|engine/i);
    });
  });
});
