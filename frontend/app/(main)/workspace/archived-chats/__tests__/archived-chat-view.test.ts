import { describe, expect, it } from 'vitest';
import type { ConversationMessage } from '@/chat/types';
import { buildMessagePairs } from '../components/archived-chat-view';

const access = { recordId: 'r1', kind: 'native' as const, endpoint: '/api/v1/access-requests' };

describe('archived chat view keeps the redaction the server applied', () => {
  it('carries hidden sources, hidden details, hidden attachments and who asked into the pair', () => {
    const messages = [
      {
        _id: 'q1',
        messageType: 'user_query',
        content: 'What about it?',
        askedBy: { userId: 'u1', name: 'Alice' },
        attachments: [{ redacted: true, reason: 'no_access', requestAccess: access }],
      },
      {
        _id: 'a1',
        messageType: 'bot_response',
        content: '',
        detailsHidden: true,
        redaction: { redacted: true, partial: false, reason: 'no_access' },
        citations: [{ citationId: 'c1', redacted: true, reason: 'no_access', requestAccess: access }],
      },
    ] as unknown as ConversationMessage[];

    const [pair] = buildMessagePairs(messages);
    expect(pair?.citationMaps.redaction?.redacted).toBe(true);
    expect(pair?.citationMaps.redaction?.sources.map((s) => s.recordId)).toEqual(['r1']);
    expect(pair?.citationMaps.detailsHidden).toBe(true);
    expect(pair?.redactedAttachments.map((a) => a.recordId)).toEqual(['r1']);
    expect(pair?.askedBy?.name).toBe('Alice');
  });

  it('keeps an unanswered question and a plain answer working', () => {
    const [unanswered] = buildMessagePairs([{ _id: 'q', messageType: 'user_query', content: 'hi' }] as unknown as ConversationMessage[]);
    expect(unanswered?.answer).toBe('');
    expect(unanswered?.citationMaps.redaction).toBeUndefined();
  });
});
