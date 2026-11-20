import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
/** The question block above an answer: the question, its time, its filter pills, then the answer. */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const response = vi.hoisted(() => ({ extra: {} as Record<string, unknown> }));

vi.mock('@/config', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/config')>();
  return {
    ...real,
    chatSlots: {
      ...real.chatSlots,
      useResponseExtension: () => ({ hideTabs: false, hideConfidence: false, allowRegenerate: true, ...response.extra }),
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
import { AnswerPanelScope } from '../../answer-panel/answer-panel-store';

afterEach(() => {
  cleanup();
  response.extra = {};
});

const SENT = new Date();
SENT.setHours(13, 40, 0, 0);
const CLOCK = SENT.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });

function row(props: Partial<React.ComponentProps<typeof ChatResponse>> = {}, scope: { column: boolean } | null = null) {
  return render(
    <Theme>
      <AnswerPanelScope.Provider value={scope}>
      <ChatResponse
        question="What changed?"
        answer="Two pages."
        messageId="row-1"
        citationMessageRowKey="row-1"
        isLastMessage
        createdAt={SENT.toISOString()}
        {...props}
      />
      </AnswerPanelScope.Provider>
    </Theme>,
  );
}

describe('the time under the question', () => {
  it('shows when the question was sent, under the question, in a chat', () => {
    row();
    const time = screen.getByTestId('question-time');
    expect(time.textContent).toBe(CLOCK);
    expect(time.getAttribute('dateTime')).toBe(SENT.toISOString());
    const question = screen.getByTestId('user-query-heading').closest('[data-chat-question]') as HTMLElement;
    expect(question.contains(time)).toBe(true);
  });

  it('gives way to the line an extension draws instead, as in a shared chat whose byline carries the time', () => {
    response.extra = { questionMeta: false };
    row();
    expect(screen.queryByTestId('question-time')).toBeNull();
  });

  it('is not drawn for a question with no time on record', () => {
    row({ createdAt: undefined });
    expect(screen.queryByTestId('question-time')).toBeNull();
  });
});

describe('no line under the question: the answer starts 16 px under it', () => {
  const line = () => document.querySelector('[data-testid="question-line"], .rt-Separator');
  const question = () => screen.getByTestId('user-query-heading').closest('[data-chat-question]') as HTMLElement;
  const answerBox = () => screen.getByText('Two pages.').closest('.answer-lead-tight') as HTMLElement | null;
  const PILLS = { apps: [{ id: 'c1', name: 'Engineering handbook', nodeType: 'app', connector: 'KB' }], kb: [] };

  it('desktop chat: question, time, then the answer 16 px under it, with nothing drawn between', () => {
    row({ appliedFilters: PILLS }, { column: true });
    expect(line()).toBeNull();
    expect(screen.queryByRole('tablist')).toBeNull();
    // The question's sources are in the column's Context tab, not under it.
    expect(screen.queryByText('Engineering handbook')).toBeNull();
    expect(question().style.marginBottom).toBe('0px');
    const box = answerBox() as HTMLElement;
    expect(box).toBeTruthy();
    expect(box.style.marginTop).toBe('var(--space-4)');
    expect(question().nextElementSibling).toBe(box);
  });

  it('its level badge, first under the question, stands 16 px too, not the 24 it would merge to', () => {
    row({}, { column: true });
    const css = readFileSync(resolve(process.cwd(), 'app/globals.css'), 'utf8');
    expect(css).toMatch(/\.answer-lead-tight > :first-child \{\s*--answer-lead-gap: var\(--space-4\);/);
    expect(css).not.toMatch(/question-line/);
    expect(answerBox()).toBeTruthy();
  });

  it('a shared block at desktop width: the same', () => {
    response.extra = { headline: { compact: false }, hideTabs: true, questionMeta: false };
    row();
    expect(line()).toBeNull();
    expect(question().style.marginBottom).toBe('0px');
    expect((answerBox() as HTMLElement).style.marginTop).toBe('var(--space-4)');
  });

  it('a narrow screen narrowed to sources: the chips sit under the question, the answer follows with no line', () => {
    row({ appliedFilters: PILLS }, { column: false });
    expect(screen.getByText('Engineering handbook')).toBeTruthy();
    expect(question().style.marginBottom).toBe('var(--space-3)');
    expect(line()).toBeNull();
  });

  it('a narrow screen is as it was: the question keeps its 24 px to the inline tabs, the answer 24 under them', () => {
    row({}, { column: false });
    expect(line()).toBeNull();
    expect(screen.getByRole('tablist')).toBeTruthy();
    expect(question().style.marginBottom).toBe('var(--space-5)');
    expect(answerBox()).toBeNull();
  });

  it('a shared block on a narrow screen is as it was', () => {
    response.extra = { headline: { compact: true }, hideTabs: true, questionMeta: false };
    row({}, { column: false });
    expect(line()).toBeNull();
    expect(question().style.marginBottom).toBe('var(--space-2)');
  });

  it('a question with no answer yet has no line either', () => {
    row({ unanswered: true }, { column: true });
    expect(line()).toBeNull();
  });
});
