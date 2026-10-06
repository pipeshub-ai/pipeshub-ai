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

  it('streams a resume onto the matching older card row, not the last greeting', () => {
    const pairs = buildMessagePairs(
      [
        user('u1', 'ask me question'),
        assistant('a1', ''),
        user('u2', 'hiii'),
        assistant('a2', 'Hi! How can I help you today?'),
      ],
      { ...OPTIONS, isStreaming: true, streamingQuestion: 'ask me question' }
    );

    expect(pairs).toHaveLength(2);
    expect(pairs[0].isStreaming).toBe(true);
    expect(pairs[1].isStreaming).toBe(false);
  });
});

describe('buildMessagePairs, collaboration fields', () => {
  const alice = { userId: 'a', displayName: 'Alice' };
  const bob = { userId: 'b', displayName: 'Bob' };

  it('carries the question author and the answer requester onto the pair', () => {
    const [pair] = buildMessagePairs(
      [
        { ...user('u1', 'Q'), metadata: { custom: { author: alice } } },
        { ...assistant('a1', 'A'), metadata: { custom: { messageId: 'a1', requestedBy: bob } } },
      ],
      OPTIONS
    );

    expect(pair.author).toEqual(alice);
    expect(pair.requestedBy).toEqual(bob);
  });

  it('keeps a null author, and carries the author onto a question with no answer', () => {
    const pairs = buildMessagePairs(
      [{ ...user('u1', 'Q'), metadata: { custom: { author: null } } }],
      OPTIONS
    );

    expect(pairs[0]).toHaveProperty('author', null);
  });

  it('adds neither key to a solo pair', () => {
    const [pair] = buildMessagePairs([user('u1', 'Q'), assistant('a1', 'A')], OPTIONS);

    expect(pair).not.toHaveProperty('author');
    expect(pair).not.toHaveProperty('requestedBy');
  });

  describe('notes', () => {
    const note = (id: string, text: string, author?: unknown) => ({
      id,
      role: 'system',
      content: [{ type: 'text', text }],
      metadata: { custom: { messageType: 'note', createdAt: '2026-10-01T00:00:00.000Z', ...(author !== undefined ? { author } : {}) } },
    });

    it('a note is its own entry with its author, and no answer', () => {
      const bob = { userId: 'u-bob', displayName: 'Bob' };
      const pairs = buildMessagePairs([user('u1', 'Q'), assistant('a1', 'A'), note('n1', 'fyi <@user:u-x>', bob)], OPTIONS);
      expect(pairs.map((p) => [p.key, p.note ?? false])).toEqual([['a1', false], ['n1', true]]);
      expect(pairs[1]).toMatchObject({ question: 'fyi <@user:u-x>', answer: '', author: bob, isStreaming: false });
    });

    it('MN-10: a note that landed between a question and its answer does not break the pair', () => {
      const pairs = buildMessagePairs([user('u1', 'Long question'), note('n1', 'side note'), assistant('a1', 'The answer')], OPTIONS);
      expect(pairs.map((p) => [p.key, p.note ?? false])).toEqual([['n1', true], ['a1', false]]);
      expect(pairs[1].question).toBe('Long question');
      expect(pairs[1].answer).toBe('The answer');
      expect(pairs.some((p) => p.unanswered)).toBe(false);
    });

    it('a question still being answered keeps streaming across a note', () => {
      const pairs = buildMessagePairs(
        [user('u1', 'Live question'), note('n1', 'side note'), assistant('a1', '')],
        { ...OPTIONS, isStreaming: true, streamingQuestion: 'Live question' },
      );
      expect(pairs.find((p) => p.key === 'a1')?.isStreaming).toBe(true);
    });

    it('a question followed only by a note is shown unanswered', () => {
      const pairs = buildMessagePairs([user('u1', 'Q'), note('n1', 'side note')], OPTIONS);
      expect(pairs.map((p) => [p.key, p.unanswered ?? false, p.note ?? false])).toEqual([['u1', true, false], ['n1', false, true]]);
    });
  });

  it('carries the agent draft and its author onto the pair', () => {
    const draft = { redacted: true, authorId: 'u-a' };
    const row = {
      ...assistant('a1', 'Drafted.'),
      metadata: { custom: { messageId: 'a1', persistedAgentDraft: draft, agentDraftAuthor: 'Alice' } },
    };
    const [pair] = buildMessagePairs([user('u1', 'make an agent'), row], OPTIONS);

    expect(pair.persistedAgentDraft).toEqual(draft);
    expect(pair.agentDraftAuthor).toBe('Alice');
  });

  it('carries the answer time, and counts the notes posted between a question and its answer', () => {
    const noteRow = (id: string) => ({
      id,
      role: 'system',
      content: [{ type: 'text', text: 'hi' }],
      metadata: { custom: { messageType: 'note' } },
    });
    const answer = { ...assistant('a1', 'A'), metadata: { custom: { messageId: 'a1', createdAt: '2026-09-18T10:02:00Z' } } };
    const pairs = buildMessagePairs([user('u1', 'Q'), noteRow('n1'), noteRow('n2'), answer], OPTIONS);
    const pair = pairs.find((p) => p.key === 'a1');
    expect(pair?.answeredAt).toBe('2026-09-18T10:02:00Z');
    expect(pair?.interleavedNotes).toBe(2);

    const [plain] = buildMessagePairs([user('u1', 'Q'), assistant('a1', 'A')], OPTIONS);
    expect(plain.interleavedNotes).toBeUndefined();
    expect(plain.answeredAt).toBeUndefined();
  });
});
