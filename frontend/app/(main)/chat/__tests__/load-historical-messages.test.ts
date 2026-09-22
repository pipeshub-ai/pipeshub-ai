/**
 * Unit tests for `loadHistoricalMessages`: how a stored conversation becomes
 * the thread shown after a reload.
 */
import { describe, it, expect, vi } from 'vitest';
import type { ConversationMessage } from '../types';

// `../runtime` pulls in `@/lib/api`, whose auth store reads localStorage at
// import time; nothing here makes a request.
vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const { loadHistoricalMessages } = await import('../runtime');

function message(overrides: Partial<ConversationMessage>): ConversationMessage {
  return {
    _id: 'm',
    messageType: 'user_query',
    content: '',
    contentFormat: 'MARKDOWN',
    citations: [],
    followUpQuestions: [],
    feedback: [],
    createdAt: '2026-09-18T00:00:00.000Z',
    updatedAt: '2026-09-18T00:00:00.000Z',
    ...overrides,
  } as ConversationMessage;
}

describe('loadHistoricalMessages', () => {
  it('drops a reply that was stopped before any text arrived', () => {
    // The backend saves this row when a stopped run's connection closes; the
    // live view never shows it, so a reload must not either.
    const { messages } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'A question' }),
      message({ _id: 'a', messageType: 'bot_response', content: '', status: 'stopped' }),
    ]);

    expect(messages.map((m) => m.role)).toEqual(['user']);
  });

  it('keeps a stopped reply that has text, with its Stopped status', () => {
    const { messages } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'A question' }),
      message({ _id: 'a', messageType: 'bot_response', content: 'Partial answer', status: 'stopped' }),
    ]);

    expect(messages).toHaveLength(2);
    expect(messages[1].metadata?.custom?.status).toBe('stopped');
  });

  it('keeps an empty stopped reply that has tool activity', () => {
    // A run stopped mid-tool has no answer text, but its activity transcript
    // is what the user saw and must survive a reload.
    const parts = [{ type: 'tool_call', toolCallId: 't1', toolName: 'search_knowledge_base', status: 'running' }];
    const { messages } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'A question' }),
      message({ _id: 'a', messageType: 'bot_response', content: '', status: 'stopped', parts } as Partial<ConversationMessage>),
    ]);

    expect(messages).toHaveLength(2);
    expect(messages[1].metadata?.custom?.persistedParts).toEqual(parts);
  });

  it('keeps an empty stopped reply that carries a pending question', () => {
    const payload = { name: 'ask_user_question', questions: [{ question: 'Which region?', options: ['EU', 'US'] }] };
    const { messages, unansweredAskUserQuestion } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'A question' }),
      message({
        _id: 't',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: payload }],
      } as Partial<ConversationMessage>),
      message({ _id: 'a', messageType: 'bot_response', content: '', status: 'stopped' }),
    ]);

    expect(messages.map((m) => m.role)).toEqual(['user', 'assistant']);
    expect(unansweredAskUserQuestion).not.toBeNull();
  });

  it('keeps an empty reply that was not stopped', () => {
    const { messages } = loadHistoricalMessages([
      message({ _id: 'q', messageType: 'user_query', content: 'A question' }),
      message({ _id: 'a', messageType: 'bot_response', content: '' }),
    ]);

    expect(messages).toHaveLength(2);
  });
});
