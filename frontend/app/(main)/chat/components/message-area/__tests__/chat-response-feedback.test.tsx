/** The answer offers Helpful and Not helpful unless the response extension turns them off (`allowFeedback: false`). */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const response = vi.hoisted(() => ({ allowFeedback: undefined as boolean | undefined }));

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
        ...(response.allowFeedback === undefined ? {} : { allowFeedback: response.allowFeedback }),
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
  response.allowFeedback = undefined;
});

const row = () =>
  render(
    <Theme>
      <ChatResponse question="Plot it" answer="Here is the chart." messageId="row-1" citationMessageRowKey="row-1" isLastMessage />
    </Theme>,
  );

describe('Feedback on the answer', () => {
  it('is offered by default', () => {
    row();
    expect(screen.getByRole('button', { name: 'Helpful' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Not helpful' })).toBeTruthy();
  });

  it('is not offered when the extension turns it off, and the other actions stay', () => {
    response.allowFeedback = false;
    row();
    expect(screen.queryByRole('button', { name: 'Helpful' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Not helpful' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Regenerate' })).toBeTruthy();
  });
});
