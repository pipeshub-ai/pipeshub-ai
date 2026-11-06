/** The question offers Edit unless the response extension turns it off (`allowEditQuery: false`). */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const response = vi.hoisted(() => ({ allowEditQuery: undefined as boolean | undefined }));

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
        ...(response.allowEditQuery === undefined ? {} : { allowEditQuery: response.allowEditQuery }),
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
  response.allowEditQuery = undefined;
});

const row = () =>
  render(
    <Theme>
      <ChatResponse question="Plot it" answer="Here is the chart." messageId="row-1" citationMessageRowKey="row-1" isLastMessage />
    </Theme>,
  );

describe('Edit on the question', () => {
  it('is offered by default', () => {
    row();
    expect(screen.getByRole('button', { name: 'Edit' })).toBeTruthy();
  });

  it('is not offered when the extension turns it off', () => {
    response.allowEditQuery = false;
    row();
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    // The question keeps its own Copy; the answer's Copy below has the same name.
    expect(within(screen.getByTestId('user-query-inline-actions')).getByRole('button', { name: 'Copy' })).toBeTruthy();
  });
});
