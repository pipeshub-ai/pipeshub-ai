/** Every chat draws the run's activity in one bordered card above the confidence, with no rule under it. */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { MessagePart } from '../../../types';

const response = vi.hoisted(() => ({
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
import { TRAIL_FOLD_EVENT } from '../keep-on-screen';

const PARTS: MessagePart[] = [
  { type: 'tool_call', toolCallId: 'call-1', toolName: 'web_search', status: 'completed' },
  { type: 'text', content: 'Revenue was 4.', isFinal: true },
];

const CITED = {
  citations: { c1: { citationId: 'c1', recordId: 'r1', recordName: 'q3.pdf', content: 'x', chunkIndex: 1 } },
  sources: { r1: 'c1' },
  sourcesOrder: ['r1'],
  citationsOrder: { 1: 'c1' },
} as never;

afterEach(() => {
  cleanup();
  response.headline = undefined;
});

function answer(opts: { cited?: boolean } = {}) {
  render(
    <Theme>
      <ChatResponse
        question="What is Q3?"
        answer="Revenue was 4."
        confidence="Very High"
        citationMaps={opts.cited === false ? undefined : CITED}
        persistedParts={PARTS}
        messageId="row-1"
        citationMessageRowKey="row-1"
        isLastMessage
      />
    </Theme>,
  );
  return { activity: screen.getByText('Used 1 tool') };
}

const precedes = (a: Node, b: Node) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

describe('the activity card', () => {
  it('holds the activity and its summary row in every chat, before the confidence, with no rule before the answer', () => {
    const { activity } = answer();
    const confidence = screen.getByText('Very High');
    const card = screen.getByTestId('chat-activity-card');
    expect(card.contains(activity)).toBe(true);
    expect(card.contains(confidence)).toBe(false);
    expect(precedes(card, confidence)).toBe(true);
    expect(precedes(confidence, screen.getByText('Revenue was 4.'))).toBe(true);
    expect(screen.queryByTestId('chat-activity-separator')).toBeNull();
  });

  it('spans the answer column, on a raised fill, 24 px over what follows', () => {
    answer();
    const card = screen.getByTestId('chat-activity-card');
    expect(card.style.maxWidth).toBe('');
    expect(card.style.border).toBe('1px solid var(--olive-5)');
    expect(card.style.background).toBe('var(--olive-2)');
    expect(card.style.borderRadius).toBe('var(--radius-3)');
    expect(card.style.marginBottom).toBe('var(--space-5)');
  });

  it('is not drawn for an answer with no steps', () => {
    render(
      <Theme>
        <ChatResponse
          question="What is Q3?"
          answer="Revenue was 4."
          persistedParts={[{ type: 'text', content: 'Revenue was 4.', isFinal: true }]}
          messageId="row-1"
          citationMessageRowKey="row-1"
          isLastMessage
        />
      </Theme>,
    );
    expect(screen.getByText('Revenue was 4.')).toBeTruthy();
    expect(screen.queryByTestId('chat-activity-card')).toBeNull();
  });
});

describe('the confidence badge', () => {
  it('shows the level the model gave, also on an answer that read no sources', () => {
    answer({ cited: false });
    expect(screen.getByText('Very High')).toBeTruthy();
  });

  it('tops the answer text, 24 px under the card and 8 px over the text', () => {
    answer();
    const badge = screen.getByText('Very High').parentElement!;
    // The 8 px is inside the badge's place, so it closes with the place on an answer that ends without a level.
    expect((badge.parentElement as HTMLElement).style.paddingBottom).toBe('var(--space-2)');
    const place = badge.closest('.level-slot') as HTMLElement;
    expect(place.nextElementSibling?.textContent).toBe('Revenue was 4.');
    expect(place.parentElement!.style.marginTop).toBe('var(--answer-lead-gap, var(--space-5))');
  });
});

describe('a headline question', () => {
  it('at desktop width leaves the gap to the line under it', () => {
    response.headline = { compact: false };
    answer();
    const question = screen.getByTestId('user-query-heading').closest('[data-chat-question]') as HTMLElement;
    expect(question.style.marginBottom).toBe('0px');
  });

  it('with no line, on a narrow screen, is 8 px over the answer’s own 16 (24 in all)', () => {
    response.headline = { compact: true };
    answer();
    const question = screen.getByTestId('user-query-heading').closest('[data-chat-question]') as HTMLElement;
    expect(question.style.marginBottom).toBe('var(--space-2)');
  });

  it('leaves an answer without one 16 px under the question', () => {
    answer();
    const question = screen.getByTestId('user-query-heading').closest('[data-chat-question]') as HTMLElement;
    expect(question.style.marginBottom).toBe('var(--space-4)');
  });
});

describe('the confidence badge while the answer streams', () => {
  // Streamed text that reads as the answer: a lone sentence may still be the run's narration and is held back.
  const STREAMED = 'Revenue was 4.\n\nCosts held.';
  const row = (streaming: boolean) => (
    <Theme>
      <ChatResponse
        question="What is Q3?"
        answer={streaming ? '' : 'Revenue was 4.'}
        confidence={streaming ? undefined : 'Very High'}
        isStreaming={streaming}
        streamingContent={streaming ? STREAMED : undefined}
        streamingParts={streaming ? PARTS : undefined}
        persistedParts={streaming ? undefined : PARTS}
        messageId="row-1"
        citationMessageRowKey="row-1"
        isLastMessage
      />
    </Theme>
  );

  it('keeps its place, unseen, so the level appearing at the end moves nothing', () => {
    const { container, rerender } = render(row(true));
    const slot = container.querySelector('[data-confidence-badge]') as HTMLElement;
    expect(slot.style.visibility).toBe('hidden');
    expect(slot.getAttribute('aria-hidden')).toBe('true');
    expect(slot.tabIndex).toBe(-1);
    const place = slot.closest('.level-slot') as HTMLElement;
    expect(place.dataset.open).toBe('true');
    const answerBox = place.parentElement as HTMLElement;
    expect(answerBox.style.marginTop).toBe('var(--answer-lead-gap, var(--space-5))');

    rerender(row(false));
    // The same element in the same place and box: shown, with the level, and nothing above or around it changed.
    const badge = container.querySelector('[data-confidence-badge]') as HTMLElement;
    expect(badge).toBe(slot);
    expect(badge.style.visibility).toBe('');
    expect(badge.textContent).toContain('Very High');
    expect(badge.closest('.level-slot')).toBe(place);
    expect(place.dataset.open).toBe('true');
    expect(place.parentElement).toBe(answerBox);
    expect(answerBox.style.marginTop).toBe('var(--answer-lead-gap, var(--space-5))');
  });

  it('closes the empty place with the trail\'s fold when the answer ends without a level, holding the text still', () => {
    const { container, rerender } = render(row(true));
    const slot = container.querySelector('[data-confidence-badge]') as HTMLElement;
    const place = slot.closest('.level-slot') as HTMLElement;
    const answerBox = place.parentElement as HTMLElement;
    const closes = vi.fn();
    container.addEventListener(TRAIL_FOLD_EVENT, closes);
    rerender(
      <Theme>
        <ChatResponse
          question="What is Q3?"
          answer="Revenue was 4."
          isStreaming={false}
          persistedParts={PARTS}
          messageId="row-1"
          citationMessageRowKey="row-1"
          isLastMessage
        />
      </Theme>,
    );
    // Not left empty until a reload: it closes on the fold's motion, the space above easing to the answer's own.
    expect(container.querySelector('[data-confidence-badge]')).toBe(slot);
    expect(place.dataset.open).toBe('false');
    expect(answerBox.style.marginTop).toBe('0px');
    expect(answerBox.style.transition).toContain('margin-top');
    // The scroll box around the answer is told, as for the fold, so the text the reader is on stays where it is.
    expect(closes).toHaveBeenCalled();
  });

  it('draws no empty place for a saved answer that has no level', () => {
    const { container } = render(
      <Theme>
        <ChatResponse question="What is Q3?" answer="Revenue was 4." persistedParts={PARTS} messageId="row-1" citationMessageRowKey="row-1" isLastMessage />
      </Theme>,
    );
    expect(container.querySelector('[data-confidence-badge]')).toBeNull();
  });
});
