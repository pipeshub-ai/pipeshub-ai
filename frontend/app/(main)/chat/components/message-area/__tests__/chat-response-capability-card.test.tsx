/** The `@assistant help` capability card is not a search answer: no confidence badge, no Sources/Citations tabs. */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const streamMessageForSlot = vi.hoisted(() => vi.fn());
vi.mock('../../../streaming', () => ({
  streamMessageForSlot: (...args: unknown[]) => streamMessageForSlot(...args),
}));
vi.mock('../../../runtime', () => ({
  buildStreamChatRequestForSlot: () => ({ query: 'User selections' }),
}));
vi.mock('@/lib/hooks/use-chat-speech-config', () => ({
  useChatSpeechConfig: () => ({ hasTts: false, isLoading: false }),
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('../response-tabs', () => ({ ResponseTabs: () => <div data-testid="response-tabs" /> }));
vi.mock('../confidence-indicator', () => ({ ConfidenceIndicator: () => <div data-testid="confidence" /> }));
vi.mock('../answer-content', () => ({
  AnswerContent: ({ content }: { content: string }) => <div data-testid="answer">{content}</div>,
}));
vi.mock('../expandable-user-query', () => ({
  ExpandableUserQuery: ({ question }: { question: string }) => <div>{question}</div>,
}));
vi.mock('../../../../knowledge-base/api', () => ({ KnowledgeBaseApi: {} }));
vi.mock('@/knowledge-base/api', () => ({ KnowledgeBaseApi: {} }));

import { ChatResponse } from '../chat-response';

afterEach(cleanup);

const row = (props: Partial<React.ComponentProps<typeof ChatResponse>> = {}) =>
  render(
    <Theme>
      <ChatResponse
        question="@assistant help"
        answer="I can search Drive."
        messageId="row-1"
        citationMessageRowKey="row-1"
        isLastMessage
        confidence="High"
        createdAt="2026-09-18T10:00:00.000Z"
        {...props}
      />
    </Theme>,
  );

describe('capability card chrome', () => {
  it('hides the confidence badge and the Sources/Citations tabs for a Capability Card', () => {
    row({ answerMatchType: 'Capability Card' });
    expect(screen.getByTestId('answer')).toBeTruthy();
    expect(screen.queryByTestId('confidence')).toBeNull();
    expect(screen.queryByTestId('response-tabs')).toBeNull();
  });

  it('keeps both for an ordinary answer', () => {
    row();
    expect(screen.getByTestId('confidence')).toBeTruthy();
    expect(screen.getByTestId('response-tabs')).toBeTruthy();
  });
});
