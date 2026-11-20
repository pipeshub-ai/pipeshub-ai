/** A response extension can add to the answer's actions (`leadingActions`): after its own buttons, before the model details. */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const response = vi.hoisted(() => ({ leadingActions: undefined as React.ReactNode }));

vi.mock('@/config', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/config')>();
  return {
    ...real,
    chatSlots: {
      ...real.chatSlots,
      useResponseExtension: () => ({
        hideTabs: false,
        hideConfidence: false,
        allowRegenerate: true,
        ...(response.leadingActions === undefined ? {} : { leadingActions: response.leadingActions }),
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

afterEach(() => {
  cleanup();
  response.leadingActions = undefined;
});

/** The buttons of the actions row's left group, by name, in order. */
function leftGroup() {
  render(
    <Theme>
      <ChatResponse
        question="Plot it"
        answer="Here is the chart."
        messageId="row-1"
        citationMessageRowKey="row-1"
        isLastMessage
        modelInfo={{ modelName: 'model-x' } as never}
      />
    </Theme>,
  );
  const left = screen.getByTestId('message-actions').firstElementChild as HTMLElement;
  return Array.from(left.children).map((el) => el.getAttribute('aria-label') ?? el.textContent);
}

describe('More actions on the answer', () => {
  it('adds nothing by default: the left group ends with the answer\'s own last action', () => {
    expect(leftGroup().at(-1)).toBe('Regenerate');
  });

  it("an extension's actions come last in the left group, after the answer's own; the model details stay on the right", () => {
    const own = leftGroup();
    cleanup();
    response.leadingActions = (
      <button type="button" aria-label="More">
        More
      </button>
    );
    expect(leftGroup()).toEqual([...own, 'More']);
    expect(screen.getByTestId('message-actions').lastElementChild?.textContent).toContain('model-x');
  });
});
