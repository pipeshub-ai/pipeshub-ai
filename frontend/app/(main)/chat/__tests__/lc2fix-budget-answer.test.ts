/**
 * LC2FIX: a reply its run's budget stopped says so, in the reader's language, instead of "Something went wrong".
 */
import { describe, expect, it } from 'vitest';
import '@/lib/__tests__/test-i18n';
import { answerErrorText } from '../harness-errors';
import { loadHistoricalMessages } from '../runtime';
import type { ConversationMessage } from '../types';

describe('an answer stopped by its spending limit', () => {
  it('is worded by its code, not as a generic failure', () => {
    expect(answerErrorText('budget_exhausted')).toBe('This reply stopped because it reached its spending limit. Ask your admin to raise the limit, or ask for less at a time.');
    expect(answerErrorText('budget_insufficient')).toBe(
      'This reply stopped because its spending limit had too little left for the next step. Ask your admin to raise the limit, or ask for less at a time.',
    );
  });

  it('a saved error answer with that code reads the same', () => {
    const { messages } = loadHistoricalMessages([
      { _id: 'u1', messageType: 'user_query', content: 'usage 260000', createdAt: '2026-10-19T12:40:00Z' },
      {
        _id: 'e1',
        messageType: 'error',
        // The worker's English is a fallback for older clients: the code is what is worded.
        content: 'worker text',
        errorCode: 'budget_exhausted',
        runId: 'run-2',
        createdAt: '2026-10-19T12:41:00Z',
      },
    ] as ConversationMessage[]);
    expect(messages[1]?.content).toEqual([{ type: 'text', text: 'This reply stopped because it reached its spending limit. Ask your admin to raise the limit, or ask for less at a time.' }]);
  });
});
