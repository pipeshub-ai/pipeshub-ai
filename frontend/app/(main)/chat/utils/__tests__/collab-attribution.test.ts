import { describe, it, expect } from 'vitest';
import { askCardReadOnlyFor, askerOf, attributionVisible, regenerateAllowed, withRequestedByViews } from '../collab-attribution';

const alice = { userId: 'a', displayName: 'Alice' };
const bob = { userId: 'b', displayName: 'Bob' };

const access = (over: Partial<Parameters<typeof regenerateAllowed>[0]> = {}) => ({
  canSend: true,
  isOwner: false,
  isCollaborative: true,
  isReadOnly: false,
  ...over,
});

describe('askerOf', () => {
  it('prefers requestedBy, including a former member, then the question author', () => {
    expect(askerOf({ requestedBy: bob, author: alice })).toBe(bob);
    expect(askerOf({ requestedBy: null, author: alice })).toBeNull();
    expect(askerOf({ author: alice })).toBe(alice);
    expect(askerOf({})).toBeUndefined();
  });
});

describe('regenerateAllowed', () => {
  it('keeps today\'s behaviour when the flag is off or the chat is solo', () => {
    expect(regenerateAllowed(access(), false, { requestedBy: bob }, 'a')).toBe(true);
    expect(regenerateAllowed(access({ isCollaborative: false }), true, { requestedBy: bob }, 'a')).toBe(true);
  });

  it('allows only the asker in a collaborative chat, the owner included', () => {
    expect(regenerateAllowed(access(), true, { requestedBy: alice }, 'a')).toBe(true);
    expect(regenerateAllowed(access(), true, { requestedBy: bob }, 'a')).toBe(false);
    expect(regenerateAllowed(access({ isOwner: true }), true, { requestedBy: bob }, 'a')).toBe(false);
  });

  it('is never offered to a reader', () => {
    expect(regenerateAllowed(access({ canSend: false, isReadOnly: true, isCollaborative: false }), true, { requestedBy: alice }, 'a')).toBe(false);
  });

  it('falls back to the owner when the row has no author information', () => {
    expect(regenerateAllowed(access({ isOwner: true }), true, {}, 'a')).toBe(true);
    expect(regenerateAllowed(access({ isOwner: false }), true, {}, 'a')).toBe(false);
    expect(regenerateAllowed(access({ isOwner: true, canSend: false }), true, {}, 'a')).toBe(false);
  });
});

describe('askCardReadOnlyFor', () => {
  it('is interactive for the person asked', () => {
    expect(askCardReadOnlyFor(bob, 'b', true)).toBeUndefined();
  });

  it('is read-only for everyone else, naming who it waits for', () => {
    expect(askCardReadOnlyFor(bob, 'a', true)).toEqual({ name: 'Bob' });
    expect(askCardReadOnlyFor(bob, null, true)).toEqual({ name: 'Bob' });
  });

  it('is read-only for a reader even when the card is theirs', () => {
    expect(askCardReadOnlyFor(bob, 'b', false)).toEqual({ name: 'Bob' });
  });

  it('is read-only when the asker has left', () => {
    expect(askCardReadOnlyFor(null, 'a', true)).toEqual({ name: null });
  });

  it('leaves an unknown asker to the server, except for a reader', () => {
    expect(askCardReadOnlyFor(undefined, 'a', true)).toBeUndefined();
    expect(askCardReadOnlyFor(undefined, 'a', false)).toEqual({ name: null });
  });
});

describe('withRequestedByViews', () => {
  const bob = { userId: 'b', displayName: 'Bob' };

  it('turns a bare id into the row\'s author when it is the same person, else into a nameless author', () => {
    const rows = withRequestedByViews([
      { requestedBy: 'b' as never, author: bob },
      { requestedBy: 'c' as never, author: bob },
      { requestedBy: 'd' as never },
    ]);
    expect(rows.map((r) => r.requestedBy)).toEqual([bob, { userId: 'c', displayName: null }, { userId: 'd', displayName: null }]);
  });

  it('keeps authors, null and missing values, and returns the same array when nothing is a bare id', () => {
    const rows = [{ requestedBy: bob }, { requestedBy: null }, {}];
    expect(withRequestedByViews(rows)).toBe(rows);
  });
});

describe('attributionVisible', () => {
  it('names every attributed turn in a shared chat, and none without author information', () => {
    expect(attributionVisible(true, true, alice, 'a')).toBe(true);
    expect(attributionVisible(true, true, undefined, 'a')).toBe(false);
  });

  it('keeps naming other people once the chat is no longer shared, but not the viewer', () => {
    expect(attributionVisible(false, true, bob, 'a')).toBe(true);
    expect(attributionVisible(false, true, null, 'a')).toBe(true);
    expect(attributionVisible(false, true, alice, 'a')).toBe(false);
  });

  it('shows nothing with the flag off', () => {
    expect(attributionVisible(false, false, bob, 'a')).toBe(false);
  });
});
