import { describe, it, expect } from 'vitest';
import { buildMessagePairs } from '../message-pairs';
import type { CitationMaps } from '../response-tabs/citations';

const EMPTY_CITATION_MAPS = {
  citations: {},
  sources: {},
  sourcesOrder: [],
  citationsOrder: {},
} as unknown as CitationMaps;

const OPTIONS = {
  isStreaming: false,
  streamingQuestion: '',
  pendingCollections: [],
  regenerateMessageId: null,
  emptyCitationMaps: EMPTY_CITATION_MAPS,
};

const user = (id: string, text: string) => ({
  id,
  role: 'user',
  content: [{ type: 'text', text }],
});

const assistant = (id: string, text: string) => ({
  id,
  role: 'assistant',
  content: [{ type: 'text', text }],
  metadata: { custom: { messageId: id } },
});

describe('buildMessagePairs', () => {
  it('pairs each question with the answer that follows it', () => {
    const pairs = buildMessagePairs(
      [user('u1', 'First question'), assistant('a1', 'First answer.')],
      OPTIONS
    );

    expect(pairs).toHaveLength(1);
    expect(pairs[0].question).toBe('First question');
    expect(pairs[0].answer).toBe('First answer.');
    expect(pairs[0].unanswered).toBeUndefined();
  });

  // Stop before the first token drops the empty assistant placeholder, and a
  // reload drops the empty stopped reply the backend saved. Both leave the
  // question as the last message, and it has to stay on screen.
  it('keeps a question that no answer follows', () => {
    const pairs = buildMessagePairs(
      [
        user('u1', 'First question'),
        assistant('a1', 'First answer.'),
        user('u2', 'A question that never gets an answer'),
      ],
      OPTIONS
    );

    expect(pairs).toHaveLength(2);
    expect(pairs[1].question).toBe('A question that never gets an answer');
    expect(pairs[1].answer).toBe('');
    expect(pairs[1].unanswered).toBe(true);
    expect(pairs[1].status).toBeUndefined();
    expect(pairs[1].isStreaming).toBe(false);
  });

  // The stopped question is not the last message for long: the next send
  // puts it in the middle of the thread, and so does a reload once the
  // follow-up has been saved. It has to stay where it was asked.
  it('keeps an unanswered question once later turns follow it', () => {
    const pairs = buildMessagePairs(
      [
        user('u1', 'First question'),
        assistant('a1', 'First answer.'),
        user('u2', 'A question that never gets an answer'),
        user('u3', 'Follow-up question'),
        assistant('a3', 'Follow-up answer.'),
      ],
      OPTIONS
    );

    expect(pairs.map((p) => p.question)).toEqual([
      'First question',
      'A question that never gets an answer',
      'Follow-up question',
    ]);
    expect(pairs[1].unanswered).toBe(true);
    expect(pairs[2].answer).toBe('Follow-up answer.');
  });

  it('does not duplicate the question while its answer is still streaming', () => {
    const pairs = buildMessagePairs(
      [user('u1', 'Live question'), { id: 'a1', role: 'assistant', content: [{ type: 'text', text: '' }] }],
      { ...OPTIONS, isStreaming: true, streamingQuestion: 'Live question' }
    );

    expect(pairs).toHaveLength(1);
    expect(pairs[0].isStreaming).toBe(true);
    expect(pairs[0].unanswered).toBeUndefined();
  });
});
