import { describe, it, expect } from 'vitest';
import type { ThreadMessageLike } from '@assistant-ui/react';
import { clientMessageIdOf, maxSeq, mergeMessagesById, revOf, seqOf } from '../merge-messages';

function row(
  id: string | undefined,
  role: 'user' | 'assistant',
  text: string,
  custom: Record<string, unknown> = {},
): ThreadMessageLike {
  return {
    ...(id !== undefined ? { id } : {}),
    role,
    content: [{ type: 'text', text }],
    metadata: { custom },
  };
}
const ids = (rows: ThreadMessageLike[]) => rows.map((r) => r.id ?? `(${clientMessageIdOf(r)})`);
const text = (r: ThreadMessageLike) => (r.content as { text: string }[])[0].text;

describe('accessors', () => {
  it('reads seq, rev and clientMessageId, ignoring bad values', () => {
    const r = row('a', 'user', 'x', { seq: 3, rev: 2, clientMessageId: 'c' });
    expect([seqOf(r), revOf(r), clientMessageIdOf(r)]).toEqual([3, 2, 'c']);
    const bad = row('b', 'user', 'x', { seq: 'x', rev: NaN, clientMessageId: '' });
    expect([seqOf(bad), revOf(bad), clientMessageIdOf(bad)]).toEqual([undefined, undefined, undefined]);
    expect(seqOf({ role: 'user', content: [] })).toBeUndefined();
  });
});

describe('maxSeq', () => {
  it('is the highest seq, or -1 when no row has one', () => {
    expect(maxSeq([])).toBe(-1);
    expect(maxSeq([row('a', 'user', 'x'), row('b', 'user', 'x', { seq: 0 })])).toBe(0);
    expect(maxSeq([row('a', 'user', 'x', { seq: 'x' }), row('b', 'user', 'x', { seq: NaN })])).toBe(-1);
    expect(maxSeq([row('a', 'user', 'x', { seq: 4 }), row('b', 'user', 'x', { seq: 9 }), row('c', 'user', 'x', { seq: 2 })])).toBe(9);
  });
});

describe('mergeMessagesById', () => {
  const base = [row('u1', 'user', 'q1', { seq: 0, rev: 1 }), row('a1', 'assistant', 'r1', { seq: 1, rev: 1 })];

  it('is idempotent: merging the same rows again returns the same array', () => {
    const incoming = [row('a1', 'assistant', 'r1', { seq: 1, rev: 1 })];
    expect(mergeMessagesById(base, incoming)).toBe(base);
    const once = mergeMessagesById(base, [row('u2', 'user', 'q2', { seq: 2, rev: 2 })]);
    expect(once).not.toBe(base);
    expect(mergeMessagesById(once, [row('u2', 'user', 'q2', { seq: 2, rev: 2 })])).toBe(once);
    expect(mergeMessagesById(base, [])).toBe(base);
  });

  it('does not mutate its inputs', () => {
    const copy = JSON.stringify(base);
    mergeMessagesById(base, [row('u2', 'user', 'q2', { seq: 2, rev: 2 })]);
    expect(JSON.stringify(base)).toBe(copy);
  });

  it('replaces a row with the same id when the incoming rev is newer', () => {
    const merged = mergeMessagesById(base, [row('a1', 'assistant', 'regenerated', { seq: 1, rev: 3 })]);
    expect(merged).toHaveLength(2);
    expect(text(merged[1])).toBe('regenerated');
  });

  it('ignores an older rev', () => {
    expect(mergeMessagesById(base, [row('a1', 'assistant', 'stale', { seq: 1, rev: 0 })])).toBe(base);
    expect(mergeMessagesById(base, [row('a1', 'assistant', 'norev', { seq: 1 })])).toBe(base);
  });

  it('replaces rows that carry no rev only when their content differs', () => {
    const plain = [row('x', 'user', 'a')];
    expect(mergeMessagesById(plain, [row('x', 'user', 'a')])).toBe(plain);
    expect(text(mergeMessagesById(plain, [row('x', 'user', 'b')])[0])).toBe('b');
    expect(text(mergeMessagesById(plain, [row('x', 'user', 'b', { rev: 1 })])[0])).toBe('b');
  });

  it('drops duplicates inside the incoming list, the last one winning', () => {
    const merged = mergeMessagesById(base, [
      row('u2', 'user', 'first', { seq: 2, rev: 2 }),
      row('u2', 'user', 'second', { seq: 2, rev: 2 }),
    ]);
    expect(ids(merged)).toEqual(['u1', 'a1', 'u2']);
    expect(text(merged[2])).toBe('second');
  });

  it('orders by seq whatever the arrival order', () => {
    const merged = mergeMessagesById(base, [
      row('a2', 'assistant', 'r2', { seq: 3, rev: 2 }),
      row('u2', 'user', 'q2', { seq: 2, rev: 2 }),
    ]);
    expect(ids(merged)).toEqual(['u1', 'a1', 'u2', 'a2']);
  });

  it('inserts a late row between existing ones', () => {
    const rows = [row('u1', 'user', 'q', { seq: 0 }), row('a3', 'assistant', 'r', { seq: 3 })];
    const merged = mergeMessagesById(rows, [row('u2', 'user', 'q2', { seq: 2, rev: 1 })]);
    expect(ids(merged)).toEqual(['u1', 'u2', 'a3']);
  });

  it('reconciles the sender\'s optimistic row by clientMessageId instead of duplicating it', () => {
    const optimistic = row(undefined, 'user', 'my question', { clientMessageId: 'cm-1', pending: true });
    const placeholder = row('pending-1', 'assistant', '', { pending: true });
    const existing = [...base, optimistic, placeholder];
    const stored = row('u9', 'user', 'my question', { seq: 4, rev: 5, clientMessageId: 'cm-1' });
    const merged = mergeMessagesById(existing, [stored]);
    expect(ids(merged)).toEqual(['u1', 'a1', 'u9', 'pending-1']);
    expect(merged.filter((m) => m.role === 'user' && text(m) === 'my question')).toHaveLength(1);
  });

  it('keeps the optimistic row and its placeholder last while another person\'s turn lands (FE-03)', () => {
    const optimistic = row(undefined, 'user', 'mine', { clientMessageId: 'cm-2', pending: true });
    const placeholder = row('pending-2', 'assistant', '', { pending: true });
    const existing = [...base, optimistic, placeholder];
    const merged = mergeMessagesById(existing, [
      row('u5', 'user', 'theirs', { seq: 2, rev: 6 }),
      row('a5', 'assistant', 'their answer', { seq: 3, rev: 6 }),
    ]);
    expect(ids(merged)).toEqual(['u1', 'a1', 'u5', 'a5', '(cm-2)', 'pending-2']);
  });

  it('appends rows without a seq at the end and keeps legacy order', () => {
    const legacy = [row('l1', 'user', 'a'), row('l2', 'assistant', 'b')];
    const merged = mergeMessagesById(legacy, [row('l3', 'user', 'c'), row('n1', 'user', 'd', { seq: 5, rev: 1 })]);
    expect(ids(merged)).toEqual(['l1', 'l2', 'l3', 'n1']);
  });

  it('does not match an optimistic row of the other role', () => {
    const existing = [row(undefined, 'user', 'q', { clientMessageId: 'x' })];
    const merged = mergeMessagesById(existing, [row('a', 'assistant', 'r', { seq: 1, clientMessageId: 'x' })]);
    expect(merged).toHaveLength(2);
  });

  it('handles unkeyed incoming rows', () => {
    const merged = mergeMessagesById([], [row(undefined, 'user', 'anon')]);
    expect(merged).toHaveLength(1);
  });
});
