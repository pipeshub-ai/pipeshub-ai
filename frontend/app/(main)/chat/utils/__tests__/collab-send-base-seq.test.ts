import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ThreadMessageLike } from '@assistant-ui/react';

vi.mock('@/lib/api', () => ({ apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }));

const { prepareCollabSend } = await import('../collab-send');
const { loadHistoricalMessages } = await import('../../runtime');
const { useFeatureFlagsStore } = await import('@/lib/store/feature-flags-store');

const stored = (over: Record<string, unknown>) =>
  ({
    messageType: 'user_query', content: 'q', contentFormat: 'MARKDOWN', citations: [], followUpQuestions: [], feedback: [],
    createdAt: 'x', updatedAt: 'x', ...over,
  }) as never;

beforeEach(() => {
  useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: true } } as never);
});

describe('prepareCollabSend on a tab that only loaded the conversation detail', () => {
  const access = { isCollaborative: true } as never;

  it('sends the highest seq the detail carried as baseSeq', () => {
    const { messages } = loadHistoricalMessages([
      stored({ _id: 'q', seq: 1, author: { userId: 'a', displayName: 'Alice' } }),
      stored({ _id: 'a', messageType: 'bot_response', seq: 2, requestedBy: 'a', author: { userId: 'a', displayName: 'Alice' } }),
    ]);
    const request = { query: 'next' } as never as { query: string; baseSeq?: number; clientMessageId?: string };
    expect(prepareCollabSend({ messages, access }, request as never)).not.toBeNull();
    expect(request.baseSeq).toBe(2);
  });

  it('sends no baseSeq when no row carries a seq (the flag-off detail shape)', () => {
    const { messages } = loadHistoricalMessages([stored({ _id: 'q' })]) as { messages: ThreadMessageLike[] };
    const request = { query: 'next' } as { query: string; baseSeq?: number };
    prepareCollabSend({ messages, access }, request as never);
    expect(request.baseSeq).toBeUndefined();
  });
});
