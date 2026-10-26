import { describe, expect, it } from 'vitest';
import type { ConversationMessage } from '@/chat/types';
import { buildMessagePairs } from '../components/archived-chat-view';

describe('archived chat message pairs', () => {
  it('builds citation maps from the answer citations', () => {
    const [pair] = buildMessagePairs([
      { _id: 'q1', messageType: 'user_query', content: 'What about it?' },
      {
        _id: 'a1',
        messageType: 'bot_response',
        content: 'It is [1].',
        citations: [{ citationId: 'c1', citationData: { _id: 'c1', content: 'chunk', chunkIndex: 1, metadata: { recordId: 'r1', recordName: 'Doc' } } }],
      },
    ] as unknown as ConversationMessage[]);
    expect(pair?.answer).toBe('It is [1].');
    expect(Object.keys(pair?.citationMaps.citations ?? {})).toHaveLength(1);
  });

  it('keeps an unanswered question and skips an empty answer instead of pairing it as a question', () => {
    const pairs = buildMessagePairs([
      { _id: 'q1', messageType: 'user_query', content: 'hi' },
      { _id: 'a1', messageType: 'bot_response', content: '' },
      { _id: 'q2', messageType: 'user_query', content: 'again' },
    ] as unknown as ConversationMessage[]);
    expect(pairs.map((p) => [p.key, p.answer])).toEqual([['q1', ''], ['q2', '']]);
    expect(Object.keys(pairs[0]?.citationMaps.citations ?? {})).toHaveLength(0);
  });
});
