import { describe, expect, it } from 'vitest';
import { inboxFeedSchema, type InboxFeed } from '@/lib/contracts/harness/generated/ext/pipeshub/api/inbox-feed';
import { inboxItemSchema, type InboxItem } from '@/lib/contracts/harness/generated/ext/pipeshub/api/inbox-item';
import feedSchemaFile from '../../../../contracts/harness/ext/pipeshub/api/inbox-feed.schema.json';
import de from '@/lib/i18n/locales/de-DE.json';
import cs from '@/lib/i18n/locales/cs-CZ.json';
import enIN from '@/lib/i18n/locales/en-IN.json';
import en from '@/lib/i18n/locales/en-US.json';
import es from '@/lib/i18n/locales/es-ES.json';
import hi from '@/lib/i18n/locales/hi-IN.json';
import ko from '@/lib/i18n/locales/ko-KR.json';
import zhCN from '@/lib/i18n/locales/zh-CN.json';
import zhSG from '@/lib/i18n/locales/zh-SG.json';
import zhTW from '@/lib/i18n/locales/zh-TW.json';
import {
  inboxActions,
  inboxGlyphLabel,
  inboxMark,
  inboxRow,
  inboxRows,
  inboxWords,
  pausedLabel,
  pausedUntil,
  readNoticeIds,
  relativeTime,
} from '../inbox';
import type { TFunction } from 'i18next';
import { interactionTitle } from '@/chat/interaction-wording';
import type { Words } from '../words';
import { words } from './fixtures';

const NOW = Date.parse('2026-11-09T12:00:00Z');

/** An item as the feed sends it, checked against the contract so every case is one the server can send. */
const item = (over: Partial<InboxItem>): InboxItem =>
  inboxItemSchema.parse({
    id: 'n:65f1a2b3c4d5e6f708192a3b',
    kind: 'run_finished',
    severity: 'success',
    waiting: false,
    unread: true,
    archived: false,
    title: 'Quarterly report',
    description: 'Finished. The answer is ready.',
    at: NOW - 5 * 60_000,
    pointer: { type: 'conversation', conversationId: 'c1', runId: 'r1' },
    link: '/chat/?conversationId=c1',
    // UIP3-FU-1: rows are worded from the typed subject; `title` is the English fallback.
    subjectVersion: 1,
    subject: { chat: 'Quarterly report', outcome: 'finished' },
    ...over,
  }) as InboxItem;

const LOCALES: Record<string, unknown> = { 'cs-CZ': cs, 'de-DE': de, 'en-IN': enIN, 'en-US': en, 'es-ES': es, 'hi-IN': hi, 'ko-KR': ko, 'zh-CN': zhCN, 'zh-SG': zhSG, 'zh-TW': zhTW };
const lookup = (catalogue: unknown, key: string): unknown => key.split('.').reduce<unknown>((n, p) => (n as Record<string, unknown> | undefined)?.[p], catalogue);
const german: Words = (key, vars = {}) => {
  const raw = lookup(de, key);
  if (typeof raw !== 'string') throw new Error(`missing de-DE key ${key}`);
  return raw.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(vars[name] ?? ''));
};

describe('a feed item as the design row', () => {
  it('the schema example reads as the design row: named by the chat, how it went, the chat mark, View run', () => {
    const example = (feedSchemaFile as { examples: InboxFeed[] }).examples[0];
    const feed = inboxFeedSchema.parse(example) as InboxFeed;
    const [row] = inboxRows(feed.items, 1_700_000_000_000 + 3 * 3_600_000, words, 'en-US');
    expect(row).toMatchObject({
      id: 'n:65f1a2b3c4d5e6f708192a3b',
      title: 'Quarterly report',
      description: 'Finished. The answer is ready.',
      time: '3 hr. ago',
      unread: true,
      waiting: false,
      kind: 'run_finished',
      icon: 'assistant',
      tone: 'accent',
      href: '/chat/?conversationId=c1',
      runId: 'r1',
    });
    expect(row.actions?.map((a) => a.id)).toEqual(['viewRun']);
  });

  it('asks sort first whatever their time, then notices as the feed sent them', () => {
    const rows = inboxRows(
      [
        item({ id: 'n:a1', at: NOW - 1000 }),
        item({ id: 'a:r1:i1', kind: 'approval', waiting: true, severity: 'warning', title: 'Approve: Create Jira issue', description: 'A task is waiting for your decision before it goes on.', at: NOW - 3_600_000, pointer: { type: 'conversation', conversationId: 'c2', runId: 'r1', interactionId: 'i1' }, link: '/chat/?conversationId=c2' }),
        item({ id: 'n:a2', at: NOW - 2000 }),
      ],
      NOW,
      words,
    );
    expect(rows.map((r) => r.id)).toEqual(['a:r1:i1', 'n:a1', 'n:a2']);
  });

  it('an approval opens its chat at the card: worded in the reader’s language from its subject', () => {
    const approval = item({ id: 'a:r1:i1', kind: 'approval', waiting: true, severity: 'warning', title: 'Approve: Create Jira issue', description: 'title: Q3 numbers', pointer: { type: 'conversation', conversationId: 'c2', runId: 'r1', interactionId: 'i1' }, link: '/chat/?conversationId=c2', subject: { action: { name: 'Create Jira issue', app: 'jira' } } });
    expect(inboxWords(approval, german)).toEqual({ title: 'Freigeben: Create Jira issue in jira', description: 'Eine Aufgabe wartet auf Ihre Entscheidung, bevor sie weitermacht.' });
    expect(inboxWords({ ...approval, subject: { action: { name: 'Run a query', app: null } } }, words).title).toBe('Approve: Run a query');
    expect(inboxActions(approval, words)).toEqual([{ id: 'openAsk', label: 'Review in chat', weight: 'primary', leaves: true }]);
    expect(inboxMark(approval)).toEqual({ icon: 'front_hand', tone: 'accent' });
    const connect = item({ ...approval, title: 'Connect Google Drive to continue', subject: { connect: 'Google Drive' } });
    expect(inboxWords(connect, words)).toEqual({ title: 'Connect Google Drive to continue', description: 'A task is waiting for you to connect this before it goes on.' });
    expect(inboxWords(connect, german).title).toBe('Google Drive verbinden, um fortzufahren');
  });

  it('CJFIX-A F4: an approval with its tool id is worded as the chat’s approval card words it, never by the id', () => {
    const ask = (action: Record<string, unknown>) =>
      item({ id: 'a:r1:i1', kind: 'approval', waiting: true, severity: 'warning', title: 'Approve: E2Esaas · create_ticket', description: 'd', pointer: { type: 'conversation', conversationId: 'c2', runId: 'r1', interactionId: 'i1' }, link: '/chat/?conversationId=c2', subject: { action: action as never } });
    const mcp = { name: 'E2Esaas · create_ticket', app: null, tool: 'mcp_e2esaas_create_ticket' };
    const title = inboxWords(ask(mcp), words).title;
    expect(title).toBe(interactionTitle(words as unknown as TFunction, { toolName: mcp.tool, displayName: mcp.name }));
    expect(title).toBe('Create ticket in E2Esaas?');
    expect(title).not.toContain('create_ticket');
    expect(inboxWords(ask({ name: 'Send message', app: 'slack', tool: 'slack__send_message', destination: '#sales' }), words).title).toBe('Send message to #sales in Slack?');
    expect(inboxWords(ask({ name: 'web_search', app: null, tool: 'web_search' }), words).title).toBe('Search the web?');
    // Every locale words it with the card's own keys: no id and no English template left over.
    for (const [name, catalogue] of Object.entries(LOCALES)) {
      const t: Words = (key, vars = {}) => {
        let raw = lookup(catalogue, key);
        if (raw === undefined && typeof vars.count === 'number') raw = lookup(catalogue, `${key}_${vars.count === 1 ? 'one' : 'other'}`);
        if (typeof raw !== 'string') throw new Error(`missing ${name} key ${key}`);
        return raw.replace(/\{\{(\w+)\}\}/g, (_, v: string) => String(vars[v] ?? ''));
      };
      const worded = inboxWords(ask(mcp), t).title;
      expect(worded, name).toContain('E2Esaas');
      expect(worded, name).not.toContain('create_ticket');
      expect(worded, name).not.toContain('{{');
    }
  });

  it('never reads the English: a title that looks like the server’s is kept as it is without a subject', () => {
    const old = item({ id: 'a:r1:i1', kind: 'approval', waiting: true, severity: 'warning', title: 'Approve: Create Jira issue', description: 'A task is waiting for your decision before it goes on.', pointer: { type: 'conversation', conversationId: 'c2' }, subject: {} });
    expect(inboxWords(old, german)).toEqual({ title: 'Approve: Create Jira issue', description: 'A task is waiting for your decision before it goes on.' });
    // A subject of a later version than this client knows: the row reads as the server wrote it.
    const later = { ...item({ subject: { chat: 'Quarterly report', outcome: 'finished' } }), subjectVersion: 2 } as unknown as InboxItem;
    expect(inboxWords(later, german)).toEqual({ title: 'Quarterly report', description: 'Finished. The answer is ready.' });
    const none = { ...item({}), subject: undefined } as unknown as InboxItem;
    expect(inboxWords(none, german).description).toBe('Finished. The answer is ready.');
  });

  it('a question is answered in the chat; its words are the question', () => {
    const q = item({ id: 'q:r1:c1', kind: 'question', waiting: true, severity: 'info', title: 'A question for you', description: 'Which region?', pointer: { type: 'conversation', conversationId: 'c3', runId: 'r1', callId: 'c1' }, link: '/chat/?conversationId=c3', subject: { question: 'Which region?' } });
    expect(inboxWords(q, german)).toEqual({ title: 'Eine Frage an Sie', description: 'Which region?' });
    expect(inboxActions(q, words)[0]).toMatchObject({ id: 'openAsk', label: 'Answer in chat' });
  });

  it('a skill proposal offers Approve, Edit and Reject in the one vocabulary; only Edit leaves', () => {
    const skill = item({ id: 's:cand-1', kind: 'skill_proposal', waiting: true, severity: 'info', title: 'New skill proposed: Triage Jira bugs', description: 'Sorts new bugs by component.', pointer: { type: 'skill_candidate', candidateId: 'cand-1' }, link: '/workspace/skills/personal/', subject: { skill: 'Triage Jira bugs' } });
    expect(inboxWords(skill, german)).toEqual({ title: 'Neuer Skill vorgeschlagen: Triage Jira bugs', description: 'Sorts new bugs by component.' });
    expect(inboxActions(skill, words)).toEqual([
      { id: 'approveSkill', label: 'Approve', weight: 'primary', leaves: false },
      { id: 'editSkill', label: 'Edit', weight: 'secondary', leaves: true },
      { id: 'rejectSkill', label: 'Reject', weight: 'no', leaves: false },
    ]);
  });

  it('an imported skill waiting for review opens its review panel; a flagged one says the scan found something (WP85-FU-1)', () => {
    const review = item({ id: 'r:skill:pdf-tools', kind: 'artefact_review', waiting: true, severity: 'info', title: 'Skill waiting for review: pdf-tools', description: 'Fill PDF forms', pointer: { type: 'catalog_item', itemKind: 'skill', name: 'pdf-tools' }, link: '/workspace/skills/personal/?review=pdf-tools', subject: { skill: 'pdf-tools' } });
    expect(inboxWords(review, words)).toEqual({ title: 'Review the imported skill pdf-tools', description: 'Fill PDF forms' });
    expect(inboxWords(item({ ...review, severity: 'warning' }), words).description).toBe('The content scan found something to check before you approve it.');
    expect(inboxActions(review, words)).toEqual([{ id: 'open', label: 'Review skill', weight: 'primary', leaves: true }]);
    expect(inboxMark(review)).toEqual({ icon: 'policy', tone: 'accent' });
    expect(inboxMark(item({ ...review, severity: 'warning' }))).toEqual({ icon: 'policy', tone: 'warning' });
  });

  it('memory reviews are worded by whose they are, and open the memory page', () => {
    const own = item({ id: 'm:mem-1', kind: 'memory_review', waiting: true, severity: 'info', title: 'Keep this memory?', description: 'Prefers EUR', pointer: { type: 'memory', memoryId: 'mem-1' }, link: '/workspace/memories/', subject: { memory: 'Prefers EUR' } });
    expect(inboxWords(own, words)).toEqual({ title: 'Keep this memory?', description: 'Prefers EUR' });
    expect(inboxWords(item({ ...own, pointer: { type: 'memory', memoryId: 'mem-1', agentKey: 'a1' } }), german).title).toBe('Ein Agent möchte sich das merken');
    expect(inboxActions(own, words)).toEqual([{ id: 'open', label: 'Review memory', weight: 'secondary', leaves: true }]);
  });

  it('finished-run notices word every outcome from the subject, and an old notice keeps its title', () => {
    expect(inboxWords(item({}), german)).toEqual({ title: 'Quarterly report', description: 'Fertig. Die Antwort ist bereit.' });
    expect(inboxWords(item({ severity: 'warning', subject: { chat: 'Q', outcome: 'cut_short' } }), words).description).toMatch(/^Finished early/);
    const failed = item({ kind: 'run_not_finished', severity: 'error', title: 'Your chat', description: "Couldn't finish.", subject: { outcome: 'not_finished' } });
    expect(inboxWords(failed, german)).toEqual({ title: 'Your chat', description: 'Nicht fertig geworden. Öffnen Sie den Chat, um zu sehen, was passiert ist, und versuchen Sie es erneut.' });
    expect(inboxMark(failed)).toEqual({ icon: 'assistant', tone: 'danger' });
  });

  it('a workflow run is named by the workflow, says how it went, offers View run and Mute workflow', () => {
    const ran = item({ id: 'n:w1', kind: 'schedule_ran', title: 'A workflow ran', description: '"Escalation digest": 3 open escalations.', pointer: { type: 'schedule', scheduleId: 's1', runId: 'r9', conversationId: 'c9' }, link: '/chat/?conversationId=c9', muted: false, subject: { workflow: 'Escalation digest', outcome: 'ran' } });
    const row = inboxRow(ran, NOW, words);
    expect(row).toMatchObject({ title: 'Escalation digest', description: 'Ran.', icon: 'account_tree', workflow: 'Escalation digest', muteId: 's1', muted: false });
    expect(inboxWords(ran, german).description).toBe(german('ui.inbox.words.workflowRan'));
    expect(row.actions?.map((a) => [a.id, a.leaves])).toEqual([
      ['viewRun', true],
      ['muteWorkflow', false],
    ]);
    expect(inboxActions({ ...ran, muted: true }, words).map((a) => a.label)).toEqual(['View run', 'Unmute workflow']);
    expect(inboxWords(item({ ...ran, kind: 'schedule_failed', subject: { workflow: 'Escalation digest', outcome: 'failed' } }), words).description).toBe("Couldn't finish.");
  });

  it('a firing that never started and one waiting on an approval are worded from the subject', () => {
    const refused = item({ id: 'n:w2', kind: 'schedule_failed', severity: 'warning', title: 'A scheduled run could not start', description: '"Nightly sync" did not run.', pointer: { type: 'schedule', scheduleId: 's2' }, link: '/workspace/schedules/', subject: { workflow: 'Nightly sync', outcome: 'refused', reason: 'workflow_changed' } });
    // UIADOPT2-FU-2: each reason of the closed set is worded; a notice with none keeps the general line.
    expect(inboxWords(refused, words)).toEqual({ title: 'Nightly sync', description: "Didn't run: the workflow was changed, paused or removed before its time." });
    expect(inboxWords(item({ ...refused, subject: { workflow: 'Nightly sync', outcome: 'refused', reason: 'budget_reached' } }), words).description).toBe("Didn't run: a spending limit was reached.");
    expect(inboxWords(item({ ...refused, subject: { workflow: 'Nightly sync', outcome: 'refused' } }), words).description).toBe("Didn't run. Open the schedules page to see why in its history.");
    const deadline = Date.parse('2026-11-09T18:00:00.000Z');
    const waiting = item({ id: 'n:w3', kind: 'schedule_waiting', severity: 'warning', title: 'A scheduled run is waiting for your approval', description: 'x', pointer: { type: 'schedule', scheduleId: 's3', runId: 'r3', conversationId: 'c3' }, link: '/chat/?conversationId=c3', subject: { workflow: 'Weekly invoices', outcome: 'waiting', deadlineAt: deadline } });
    const words3 = inboxWords(waiting, words, 'en-US');
    expect(words3.title).toBe('Weekly invoices');
    expect(words3.description).toMatch(/^Paused before a write\. Approve or deny it by Mon,? \d{1,2}:00\s?(AM|PM), or it stops then\.$/);
    expect(inboxWords(item({ ...waiting, subject: { workflow: 'Weekly invoices', outcome: 'waiting' } }), words).description).toBe('Paused before a write. Approve or deny it, or it stops.');
    expect(inboxMark(waiting).tone).toBe('warning');
  });

  it('a share names who shared what, in the reader’s language', () => {
    const share = item({ id: 'n:s1', kind: 'share', severity: 'info', title: 'Priya Raghavan shared an agent chat with you', description: 'x', pointer: { type: 'conversation', conversationId: 'c5', agentKey: 'a1' }, link: '/chat/?agentId=a1&conversationId=c5', subject: { sharedBy: 'Priya Raghavan', chat: 'Standup notes' } });
    expect(inboxWords(share, german)).toEqual({ title: 'Priya Raghavan hat einen Agenten-Chat mit Ihnen geteilt', description: 'Sie können jetzt „Standup notes“ öffnen.' });
    expect(inboxWords(item({ ...share, pointer: { type: 'conversation', conversationId: 'c5' }, subject: {} }), words)).toEqual({ title: 'A colleague shared a chat with you', description: 'You can now open it from your shared chats.' });
    expect(inboxActions(share, words)).toEqual([{ id: 'open', label: 'Open chat', weight: 'secondary', leaves: true }]);
  });

  it('a mention names its chat', () => {
    const mention = item({ id: 'n:m1', kind: 'mention', severity: 'info', title: 'You were mentioned', description: 'x', pointer: { type: 'conversation', conversationId: 'c6' }, subject: { chat: 'Launch plan' } });
    expect(inboxWords(mention, words)).toEqual({ title: 'You were mentioned', description: 'In “Launch plan”' });
  });

  it('another service’s notice keeps its own words; a link to nowhere offers nothing to follow', () => {
    const sync = item({ id: 'n:x1', kind: 'connector', severity: 'error', title: 'Connector token expired', description: 'Re-authenticate Slack to resume syncing.', pointer: { type: 'link', path: '/' }, link: '/', subject: { source: 'connectors' } });
    const row = inboxRow(sync, NOW, words);
    expect(row).toMatchObject({ title: 'Connector token expired', description: 'Re-authenticate Slack to resume syncing.', icon: 'error_outline', tone: 'danger' });
    expect(row.href).toBeUndefined();
    expect(row.actions).toEqual([]);
  });

  it('every kind of the contract is worded from its subject in all ten locales', () => {
    const keys = ['approvalTitle', 'approvalTitleIn', 'connectTitle', 'approvalWaiting', 'connectWaiting', 'questionTitle', 'memoryOwnTitle', 'memoryAgentTitle', 'skillTitle', 'skillReviewTitle', 'skillReviewFlagged', 'finished', 'finishedEarly', 'notFinished', 'workflowRan', 'workflowFailed', 'workflowRefused', 'workflowWaiting', 'workflowWaitingBy', 'sharedChat', 'sharedAgentChat', 'sharedOpen', 'sharedOpenList', 'colleague', 'mentionTitle', 'mentionIn'];
    for (const [lang, catalogue] of Object.entries(LOCALES)) for (const key of keys) expect(typeof lookup(catalogue, `ui.inbox.words.${key}`), `${lang} ${key}`).toBe('string');
    const reasons = inboxItemSchema.shape.subject.shape.reason.unwrap().options;
    for (const [lang, catalogue] of Object.entries(LOCALES)) for (const reason of reasons) expect(typeof lookup(catalogue, `ui.inbox.words.workflowRefusedWhy.${reason}`), `${lang} ${reason}`).toBe('string');
  });

  it('an archived notice offers Restore only', () => {
    expect(inboxActions(item({ archived: true, unread: false }), words)).toEqual([{ id: 'restore', label: 'Restore', weight: 'secondary', leaves: false }]);
  });

  it('Archive read takes read notices only, never an ask or an unread notice', () => {
    const ids = readNoticeIds([
      item({ id: 'n:r1', unread: false }),
      item({ id: 'n:u1', unread: true }),
      item({ id: 'a:r1:i1', kind: 'approval', waiting: true, unread: true, severity: 'warning', pointer: { type: 'conversation', conversationId: 'c2' } }),
    ]);
    expect(ids).toEqual(['n:r1']);
  });
});

describe('time and the pause in words', () => {
  it('how long ago, in the reader’s language', () => {
    expect(relativeTime(NOW - 20_000, NOW, 'en-US', words)).toBe('now');
    expect(relativeTime(NOW - 5 * 60_000, NOW, 'en-US', words)).toBe('5 min. ago');
    expect(relativeTime(NOW - 26 * 3_600_000, NOW, 'en-US', words)).toBe('yesterday');
    expect(relativeTime(NOW - 5 * 60_000, NOW, 'de-DE', german)).toBe('vor 5 Min.');
    expect(relativeTime(NOW - 30 * 86_400_000, NOW, 'en-US', words)).toBe('Oct 10');
  });

  it('paused until a time, until tomorrow, or until resumed; an expired pause is not a pause', () => {
    expect(pausedUntil({ pause: null }, NOW)).toBeNull();
    expect(pausedUntil({ pause: { untilResumed: true } }, NOW)).toBe(Infinity);
    expect(pausedUntil({ pause: { until: NOW + 1000 } }, NOW)).toBe(NOW + 1000);
    expect(pausedUntil({ pause: { until: NOW - 1000 } }, NOW)).toBeNull();
    expect(pausedLabel(Infinity, NOW, 'en-US', words)).toBe('you turn them back on');
    expect(pausedLabel(NOW + 2 * 86_400_000, NOW, 'en-US', words)).toMatch(/^tomorrow at /);
  });

  it('the glyph says paused, and counts asks only then', () => {
    expect(inboxGlyphLabel(0, false, words)).toBe('Inbox');
    expect(inboxGlyphLabel(4, false, words)).toBe('Inbox, 4 unread');
    expect(inboxGlyphLabel(0, true, words)).toBe('Inbox, notifications paused');
    expect(inboxGlyphLabel(1, true, words)).toBe('Inbox, notifications paused, 1 waiting for you');
  });
});
