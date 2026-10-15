/** A reply a delegate agent wrote directly says so in its header; replies the assistant wrote say nothing. */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));
vi.mock('../../../runtime', () => ({ buildStreamChatRequestForSlot: () => ({ query: 'q' }) }));
vi.mock('@/lib/hooks/use-chat-speech-config', () => ({
  useChatSpeechConfig: () => ({ hasTts: false, isLoading: false }),
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('../response-tabs', () => ({ ResponseTabs: () => <div data-testid="response-tabs" /> }));
vi.mock('../confidence-indicator', () => ({ ConfidenceIndicator: () => <div /> }));
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
        question="Plot it"
        answer="Here is the chart."
        messageId="row-1"
        citationMessageRowKey="row-1"
        isLastMessage
        confidence="High"
        createdAt="2026-09-18T10:00:00.000Z"
        {...props}
      />
    </Theme>,
  );

describe('answered-via label', () => {
  it('names the coding agent', () => {
    row({ answeredVia: 'coding_agent' });
    expect(screen.getByTestId('answered-via-label').textContent).toBe('via coding agent');
  });

  it('names web research', () => {
    row({ answeredVia: 'web_agent' });
    expect(screen.getByTestId('answered-via-label').textContent).toBe('via web research');
  });

  it('falls back to the delegate name for one it does not know', () => {
    row({ answeredVia: 'calendar_agent' });
    expect(screen.getByTestId('answered-via-label').textContent).toBe('via calendar_agent');
  });

  it('renders nothing when the assistant wrote the answer', () => {
    row();
    expect(screen.queryByTestId('answered-via-label')).toBeNull();
  });

  it('renders nothing while the answer is still streaming', () => {
    row({ answeredVia: 'coding_agent', isStreaming: true });
    expect(screen.queryByTestId('answered-via-label')).toBeNull();
  });
});
