/** A response extension can draw the run's activity in a bordered card above the confidence (`activityCard`). */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { MessagePart } from '../../../types';

const response = vi.hoisted(() => ({
  activityCard: undefined as boolean | undefined,
  headline: undefined as { compact: boolean } | undefined,
}));

vi.mock('@/config', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/config')>();
  return {
    ...real,
    chatSlots: {
      ...real.chatSlots,
      useResponseExtension: () => ({
        hideTabs: response.headline !== undefined,
        hideConfidence: false,
        allowRegenerate: true,
        ...(response.activityCard === undefined ? {} : { activityCard: response.activityCard }),
        ...(response.headline === undefined ? {} : { headline: response.headline }),
      }),
    },
  };
});
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));
vi.mock('../../../runtime', () => ({ buildStreamChatRequestForSlot: () => ({ query: 'q' }) }));
vi.mock('@/lib/hooks/use-chat-speech-config', () => ({
  useChatSpeechConfig: () => ({ hasTts: false, isLoading: false }),
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('../answer-content', () => ({
  AnswerContent: ({ content }: { content: string }) => <div>{content}</div>,
}));
vi.mock('@/knowledge-base/api', () => ({ KnowledgeBaseApi: {} }));

import { ChatResponse } from '../chat-response';

const PARTS: MessagePart[] = [
  { type: 'tool_call', toolCallId: 'call-1', toolName: 'web_search', status: 'completed' },
  { type: 'text', content: 'Revenue was 4.', isFinal: true },
];

afterEach(() => {
  cleanup();
  response.activityCard = undefined;
  response.headline = undefined;
});

function answer() {
  render(
    <Theme>
      <ChatResponse
        question="What is Q3?"
        answer="Revenue was 4."
        confidence="Very High"
        persistedParts={PARTS}
        messageId="row-1"
        citationMessageRowKey="row-1"
        isLastMessage
      />
    </Theme>,
  );
  return { confidence: screen.getByText('Very High'), activity: screen.getByText('Used 1 tool') };
}

const precedes = (a: Node, b: Node) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

describe('the activity card', () => {
  it('is off by default: the confidence, then the activity, then a separator before the answer', () => {
    const { confidence, activity } = answer();
    const separator = screen.getByTestId('chat-activity-separator');
    expect(screen.queryByTestId('chat-activity-card')).toBeNull();
    expect(precedes(confidence, activity)).toBe(true);
    expect(precedes(activity, separator)).toBe(true);
    expect(precedes(separator, screen.getByText('Revenue was 4.'))).toBe(true);
  });

  it('when set, holds the activity and its summary row, before the confidence, with no separator', () => {
    response.activityCard = true;
    const { confidence, activity } = answer();
    const card = screen.getByTestId('chat-activity-card');
    expect(card.contains(activity)).toBe(true);
    expect(card.contains(confidence)).toBe(false);
    expect(precedes(card, confidence)).toBe(true);
    expect(precedes(confidence, screen.getByText('Revenue was 4.'))).toBe(true);
    expect(screen.queryByTestId('chat-activity-separator')).toBeNull();
  });

  it('is at most 640 px wide, on a raised fill', () => {
    response.activityCard = true;
    answer();
    const card = screen.getByTestId('chat-activity-card');
    expect(card.style.maxWidth).toBe('640px');
    expect(card.style.background).toBe('var(--olive-2)');
  });
});

describe('a headline question', () => {
  it('is 8 px over the answer’s own 16 (24 in all), and the confidence 8 px over the text', () => {
    response.activityCard = true;
    response.headline = { compact: false };
    const { confidence } = answer();
    const question = screen.getByTestId('user-query-heading').closest('[data-chat-question]') as HTMLElement;
    expect(question.style.marginBottom).toBe('var(--space-2)');
    expect(confidence.parentElement!.style.marginBottom).toBe('var(--space-2)');
  });

  it('leaves an answer without one as it was: 16 px under the question, 12 under the confidence', () => {
    const { confidence } = answer();
    const question = screen.getByTestId('user-query-heading').closest('[data-chat-question]') as HTMLElement;
    expect(question.style.marginBottom).toBe('var(--space-4)');
    expect(confidence.parentElement!.style.marginBottom).toBe('var(--space-3)');
  });
});
