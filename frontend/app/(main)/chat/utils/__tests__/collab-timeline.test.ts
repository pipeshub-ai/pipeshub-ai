import { describe, it, expect } from 'vitest';
import { buildTimeline, resolveResponder, GROUP_WINDOW_MS } from '../collab-timeline';
import { dayLabel } from '../../components/message-area/timeline/day-divider';
import type { MessagePair } from '../../components/message-area/message-pairs';
import { emptyCitationMaps } from '../../components/message-area/response-tabs/citations';

const alice = { userId: 'a', displayName: 'Alice' };
const bob = { userId: 'b', displayName: 'Bob' };

const pair = (key: string, extra: Partial<MessagePair> = {}): MessagePair => ({
  key,
  question: `q-${key}`,
  answer: '',
  citationMaps: emptyCitationMaps,
  isStreaming: false,
  ...extra,
});
const note = (key: string, createdAt: string, author = alice) => pair(key, { note: true, createdAt, author });
const qa = (key: string, createdAt: string, author = alice, extra: Partial<MessagePair> = {}) =>
  pair(key, { createdAt, author, answer: 'a', answeredAt: new Date(new Date(createdAt).getTime() + 5000).toISOString(), ...extra });

const headers = (pairs: MessagePair[]) =>
  buildTimeline(pairs).flatMap((g) => g.rows).filter((r) => r.kind === 'human').map((r) => (r.kind === 'human' ? r.showHeader : null));

describe('grouping', () => {
  it('collapses consecutive messages from one author inside 5 minutes', () => {
    const t0 = new Date('2026-09-18T10:00:00Z').getTime();
    const iso = (ms: number) => new Date(t0 + ms).toISOString();
    expect(headers([note('1', iso(0)), note('2', iso(60_000)), note('3', iso(60_000 + GROUP_WINDOW_MS))])).toEqual([true, false, false]);
  });

  it('starts a new group after more than 5 minutes, for another author, or after a reply', () => {
    const t0 = new Date('2026-09-18T10:00:00Z').getTime();
    const iso = (ms: number) => new Date(t0 + ms).toISOString();
    expect(headers([note('1', iso(0)), note('2', iso(GROUP_WINDOW_MS + 1))])).toEqual([true, true]);
    expect(headers([note('1', iso(0)), note('2', iso(1000), bob)])).toEqual([true, true]);
    expect(headers([qa('1', iso(0)), note('2', iso(10_000))])).toEqual([true, true]);
  });

  it('never groups a former member or an unknown author', () => {
    const t0 = new Date('2026-09-18T10:00:00Z').getTime();
    const a = pair('1', { note: true, createdAt: new Date(t0).toISOString(), author: null });
    const b = pair('2', { note: true, createdAt: new Date(t0 + 1000).toISOString(), author: null });
    const c = pair('3', { note: true, createdAt: new Date(t0 + 2000).toISOString() });
    expect(headers([a, b, c])).toEqual([true, true, true]);
  });
});

describe('day dividers', () => {
  it('puts a divider above the first row and on each new local day, and breaks a group across midnight', () => {
    const d1 = new Date(2026, 8, 17, 23, 58).toISOString();
    const d2 = new Date(2026, 8, 18, 0, 1).toISOString();
    const rows = buildTimeline([note('1', d1), note('2', d2)]).flatMap((g) => g.rows);
    expect(rows.map((r) => Boolean(r.dayDivider))).toEqual([true, true]);
    expect(rows[1].kind === 'human' && rows[1].showHeader).toBe(true);
  });

  it('does not repeat the divider inside a day', () => {
    const rows = buildTimeline([note('1', new Date(2026, 8, 18, 9, 0).toISOString()), note('2', new Date(2026, 8, 18, 17, 0).toISOString())]).flatMap((g) => g.rows);
    expect(rows.map((r) => Boolean(r.dayDivider))).toEqual([true, false]);
  });

  it('labels Today, Yesterday and other days', () => {
    const now = new Date(2026, 8, 18, 12, 0);
    const label = (d: Date) => dayLabel(d.toISOString(), now, 'en-US', 'Today', 'Yesterday');
    expect(label(new Date(2026, 8, 18, 0, 5))).toBe('Today');
    expect(label(new Date(2026, 8, 17, 23, 55))).toBe('Yesterday');
    expect(label(new Date(2026, 8, 10, 9, 0))).toBe('Thursday, September 10');
    expect(label(new Date(2025, 8, 10, 9, 0))).toContain('2025');
  });
});

describe('question and reply rows', () => {
  it('draws a question and its reply as two rows of one group', () => {
    const [g] = buildTimeline([qa('1', '2026-09-18T10:00:00Z')]);
    expect(g.refKey).toBe('1');
    expect(g.rows.map((r) => r.kind)).toEqual(['human', 'reply']);
    expect(g.rows[1].kind === 'reply' && g.rows[1].replyingTo).toBeUndefined();
  });

  it('puts the question where it was asked and says whom the reply answers when notes came between', () => {
    const groups = buildTimeline([
      note('n1', '2026-09-18T10:00:30Z', bob),
      qa('q1', '2026-09-18T10:00:00Z', alice, { interleavedNotes: 1 }),
    ]);
    expect(groups.map((g) => g.key)).toEqual(['q1:question', 'n1', 'q1']);
    expect(groups[0].refKey).toBeNull();
    const reply = groups[2].rows[0];
    expect(reply.kind === 'reply' && reply.replyingTo).toEqual(alice);
  });

  it('has no reply row for a note or an unanswered question', () => {
    const groups = buildTimeline([note('n', '2026-09-18T10:00:00Z'), pair('u', { unanswered: true, createdAt: '2026-09-18T10:01:00Z', author: alice })]);
    expect(groups.flatMap((g) => g.rows).map((r) => r.kind)).toEqual(['human', 'human']);
  });
});

describe('resolveResponder', () => {
  it('names a guest agent, the chat\'s own agent, or the assistant', () => {
    expect(resolveResponder({ key: 'k', name: 'Joke Buddy' }, null, null)).toEqual({ kind: 'guest', name: 'Joke Buddy' });
    expect(resolveResponder({ key: 'k' }, 'agent-1', 'Mine')).toEqual({ kind: 'guest', name: null });
    expect(resolveResponder(undefined, 'agent-1', 'HR Agent')).toEqual({ kind: 'agent', name: 'HR Agent' });
    expect(resolveResponder(undefined, null, 'ignored')).toEqual({ kind: 'assistant', name: null });
  });
});

describe('a retried question', () => {
  const kinds = (pairs: MessagePair[]) => buildTimeline(pairs).map((g) => g.rows.map((r) => r.kind));

  it('is drawn once: the question asked again after a failed answer has no row of its own', () => {
    const failed = qa('1', '2026-09-18T10:00:00Z', alice, { question: 'same', failed: true });
    expect(kinds([failed, qa('2', '2026-09-18T10:01:00Z', alice, { question: 'same' })])).toEqual([['human', 'reply'], ['reply']]);
  });

  it('has no row while the retry is still running, so an observer does not see the question twice', () => {
    const failed = qa('1', '2026-09-18T10:00:00Z', alice, { question: 'same', failed: true });
    const pending = pair('2', { question: 'same', unanswered: true, createdAt: '2026-09-18T10:01:00Z', author: alice });
    expect(kinds([failed, pending])).toEqual([['human', 'reply']]);
  });

  it('keeps the row when the text differs, another person asks, or the earlier answer did not fail', () => {
    const failed = qa('1', '2026-09-18T10:00:00Z', alice, { question: 'same', failed: true });
    expect(kinds([failed, qa('2', '2026-09-18T10:01:00Z', alice, { question: 'other' })])[1]).toEqual(['human', 'reply']);
    expect(kinds([failed, qa('2', '2026-09-18T10:01:00Z', bob, { question: 'same' })])[1]).toEqual(['human', 'reply']);
    expect(kinds([qa('1', '2026-09-18T10:00:00Z', alice, { question: 'same' }), qa('2', '2026-09-18T10:01:00Z', alice, { question: 'same' })])[1]).toEqual(['human', 'reply']);
  });
});
