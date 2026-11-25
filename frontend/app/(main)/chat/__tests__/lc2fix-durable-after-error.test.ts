/**
 * LC2FIX: after a durable run's error answer the chat stays on the new harness, so /compact and the other
 * durable-only commands stay available. LIFECYCLE-2 saw "Compacting needs the new harness" right after a
 * budget-exhausted answer: the error answer had no run id and the chat's harness was read from it alone.
 */
import { describe, expect, it } from 'vitest';
import '@/lib/__tests__/test-i18n';
import { conversationHarness } from '../assistant-harness';
import { latestAnswerRunId } from '../activity';
import { loadHistoricalMessages } from '../runtime';
import type { ConversationMessage } from '../types';

const rows = (error: Partial<ConversationMessage>): ConversationMessage[] =>
  [
    { _id: 'u1', messageType: 'user_query', content: 'Write the notes.', createdAt: '2026-10-19T12:00:00Z' },
    { _id: 'b1', messageType: 'bot_response', content: 'Done.', runId: 'run-1', createdAt: '2026-10-19T12:00:05Z' },
    { _id: 'u2', messageType: 'user_query', content: 'usage 260000', createdAt: '2026-10-19T12:40:00Z' },
    { _id: 'e2', messageType: 'error', content: 'Something went wrong while answering.', createdAt: '2026-10-19T12:41:00Z', ...error },
  ] as ConversationMessage[];

describe('a durable chat whose latest answer is an error', () => {
  it("the error answer's run id is mapped, so the latest answer names its run", () => {
    const { messages } = loadHistoricalMessages(rows({ runId: 'run-2', errorCode: 'budget_exhausted' }));
    expect(latestAnswerRunId(messages)).toBe('run-2');
    expect(conversationHarness({ convId: 'c1', messages, isOwner: true })).toBe('durable');
  });

  it('a session the server says is durable stays durable even when the error answer has no run id (saved before the fix)', () => {
    const { messages } = loadHistoricalMessages(rows({}));
    expect(latestAnswerRunId(messages)).toBeNull();
    expect(conversationHarness({ convId: 'c1', messages, isOwner: true, durable: true })).toBe('durable');
    expect(conversationHarness({ convId: 'c1', messages, isOwner: true, durable: false })).toBe('legacy');
  });
});
