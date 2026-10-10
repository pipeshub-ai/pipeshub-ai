import { describe, it, expect, vi } from 'vitest';

vi.mock('@/lib/store/auth-store', () => ({
  useAuthStore: { getState: () => ({ isHydrated: true }) },
  hydrateAuthStore: vi.fn(),
  LOGIN_NAVIGATION_EVENT: 'pipeshub:request-login-navigation',
}));

const { collabRowCustom, readRespondingAgent } = await import('../collab-row-custom');
const { feedToThreadRows } = await import('../feed-rows');
const { loadHistoricalMessages } = await import('../../runtime');

const author = { userId: 'u1', displayName: 'Ann' };
const stored = [
  { _id: 'm1', messageType: 'user_query', content: 'q', createdAt: 'x', seq: 0, author, clientMessageId: 'c1', filesShared: true },
  { _id: 'm2', messageType: 'bot_response', content: 'a', createdAt: 'x', seq: 1, requestedBy: author },
] as never;

describe('collabRowCustom', () => {
  it('copies only the fields that are set', () => {
    expect(collabRowCustom({ _id: 'x' } as never)).toEqual({});
    expect(collabRowCustom({ _id: 'x', seq: 0, author: null, filesShared: false } as never, 3)).toEqual({
      seq: 0, rev: 3, author: null, filesShared: false,
    });
  });

  it('gives history load and feed merge the same fields, the feed adding rev', () => {
    const history = loadHistoricalMessages(stored).messages.map((m) => m.metadata?.custom);
    const feed = feedToThreadRows(stored, 5).map((m) => m.metadata?.custom);
    expect(history[0]).toMatchObject({ seq: 0, author, clientMessageId: 'c1', filesShared: true });
    expect(history[1]).toMatchObject({ seq: 1, requestedBy: author });
    expect(feed).toMatchObject(history.map((h) => ({ ...h, rev: 5 })));
    expect(history[0]).not.toHaveProperty('rev');
  });
});

describe('respondingAgent (M2)', () => {
  it('rides in the row custom for history load and the feed alike, and is tolerant of odd shapes', () => {
    const rows = [
      { _id: 'q', messageType: 'user_query', content: 'ask @joke-buddy', createdAt: 'x', seq: 0, author },
      { _id: 'b', messageType: 'bot_response', content: 'ha', createdAt: 'x', seq: 1, requestedBy: author, respondingAgent: { key: 'ag-1', name: ' Joke Buddy ', handle: 'joke-buddy', extra: 1 } },
    ] as never;
    expect(loadHistoricalMessages(rows).messages[1].metadata?.custom).toMatchObject({ respondingAgent: { key: 'ag-1', name: 'Joke Buddy', handle: 'joke-buddy' } });
    expect(feedToThreadRows(rows, 2)[1].metadata?.custom).toMatchObject({ respondingAgent: { key: 'ag-1', name: 'Joke Buddy', handle: 'joke-buddy' } });
    expect(readRespondingAgent({ key: 'k' })).toEqual({ key: 'k' });
    expect(readRespondingAgent({ key: '' })).toBeUndefined();
    expect(readRespondingAgent({ name: 'x' })).toBeUndefined();
    expect(readRespondingAgent('ag-1')).toBeUndefined();
    expect(readRespondingAgent(null)).toBeUndefined();
    expect(collabRowCustom({ _id: 'x' } as never)).not.toHaveProperty('respondingAgent');
  });
});
