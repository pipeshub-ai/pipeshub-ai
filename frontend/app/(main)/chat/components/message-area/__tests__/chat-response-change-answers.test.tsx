import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { AskUserQuestionAnswer, AskUserQuestionPayload } from '../../../types';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: {} }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));

import { ChatResponse } from '../chat-response';
import { useChatStore } from '../../../store';
import { useCommandStore } from '@/lib/store/command-store';

const PAYLOAD: AskUserQuestionPayload = {
  name: 'ask_user_question',
  questions: [
    {
      uuid: 'q1',
      question: 'Which region?',
      multiSelect: false,
      options: [
        { id: 'eu', label: 'EU', isUserInput: false },
        { id: 'us', label: 'US', isUserInput: false },
      ],
    },
    {
      uuid: 'q2',
      question: 'Which quarter?',
      multiSelect: false,
      options: [
        { id: 'q3', label: 'Q3', isUserInput: false },
        { id: 'q4', label: 'Q4', isUserInput: false },
      ],
    },
  ],
};
const ANSWERS: Record<string, AskUserQuestionAnswer> = {
  q1: { questionUuid: 'q1', selectedOptionIds: ['eu'], userInputs: {} },
  q2: { questionUuid: 'q2', selectedOptionIds: ['q3'], userInputs: {} },
};

function renderReply(props: Partial<React.ComponentProps<typeof ChatResponse>> = {}) {
  return renderInTheme(
    <ChatResponse
      question="Plan it"
      answer="Planned for EU in Q3."
      messageId="reply-1"
      isLastMessage
      persistedAskUserQuestion={PAYLOAD}
      persistedAskUserQuestionAnswers={ANSWERS}
      {...props}
    />,
  );
}

const openRegenBar = (messageId = 'reply-1') =>
  act(() => useChatStore.getState().setRegenTargetMessageId(messageId));
const nextButton = () => screen.queryByRole('button', { name: 'Next' });

let dispatched: Array<[string, unknown]>;

beforeEach(() => {
  installBrowserShims();
  dispatched = [];
  useCommandStore.setState({
    dispatch: (name: string, payload?: unknown) => {
      dispatched.push([name, payload]);
      return true;
    },
  });
  useChatStore.setState({ activeSlotId: 'slot-1', regenTargetMessageId: null, regenAnswers: null });
});

afterEach(() => {
  cleanup();
  useChatStore.setState({ activeSlotId: null, slots: {}, regenTargetMessageId: null, regenAnswers: null });
});

describe('ChatResponse: regenerating the latest reply to an answered question card', () => {
  it('opens the usual Regenerate bar, with no separate change-answers step', () => {
    renderReply();

    fireEvent.click(screen.getByRole('button', { name: 'Regenerate' }));

    expect(dispatched.map(([name]) => name)).toEqual(['showRegenBar']);
    expect(nextButton()).toBeNull();
  });

  it('lets the card be answered again while the bar is open, starting at the first question', () => {
    renderReply();
    openRegenBar();

    expect(screen.getByText(/Which region\?/)).toBeTruthy();
    expect(nextButton()).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull();
    expect(useChatStore.getState().regenAnswers).toBeNull();
  });

  it('keeps the answers picked on the card for the bar to send', () => {
    renderReply();
    openRegenBar();

    fireEvent.click(screen.getByText('US'));

    const picked = useChatStore.getState().regenAnswers;
    expect(picked?.messageId).toBe('reply-1');
    expect(picked?.message).toMatch(/^User selections:/);
    expect(picked?.message).toContain('"Which region?" → US');
    expect(picked?.message).toContain('"Which quarter?" → Q3');
    expect(dispatched).toEqual([]);
  });

  it('regenerates when the card is submitted, with its answers', () => {
    renderReply();
    openRegenBar();

    fireEvent.click(screen.getByText('US'));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));

    expect(dispatched.map(([name]) => name)).toEqual(['runRegenerate']);
    expect(useChatStore.getState().regenAnswers?.message).toContain('"Which region?" → US');
  });

  it('shows the answers being run while the reply regenerates', () => {
    useChatStore.setState({
      regenAnswers: {
        messageId: 'reply-1',
        payload: PAYLOAD,
        message: 'User selections:',
        answers: { ...ANSWERS, q1: { questionUuid: 'q1', selectedOptionIds: ['us'], userInputs: {} } },
      },
    });
    renderReply({ isStreaming: true });

    // An answered card is collapsed; open it to see the answers.
    fireEvent.click(screen.getByRole('button', { name: /show more/i }));
    // The read-only card lists every option and greys out the ones not picked.
    expect(screen.getByText('EU').getAttribute('data-accent-color')).toBe('gray');
    expect(screen.getByText('US').getAttribute('data-accent-color')).toBeNull();
  });

  it('works on the card still live right after the answer', () => {
    useChatStore.setState({
      slots: {
        'slot-1': {
          pendingAskUserQuestion: { assistantMessageId: 'reply-1', payload: PAYLOAD, answers: ANSWERS, sentAnswers: ANSWERS, status: 'persisted' },
        } as never,
      },
    });
    renderReply({ persistedAskUserQuestion: undefined, persistedAskUserQuestionAnswers: undefined });
    openRegenBar();

    fireEvent.click(screen.getByText('US'));

    expect(useChatStore.getState().regenAnswers?.message).toContain('"Which region?" → US');
  });

  it('leaves a card that is still waiting for its first answer alone', () => {
    useChatStore.setState({
      slots: {
        'slot-1': { pendingAskUserQuestion: { assistantMessageId: 'reply-1', payload: PAYLOAD, answers: {}, status: 'pending' } } as never,
      },
    });
    renderReply({ persistedAskUserQuestion: undefined, persistedAskUserQuestionAnswers: undefined });
    openRegenBar();

    fireEvent.click(screen.getByText('US'));

    expect(useChatStore.getState().regenAnswers).toBeNull();
  });

  it.each([
    ['a reply typed in the chat', { persistedAskUserQuestion: { ...PAYLOAD, repliedInChat: true } }],
    ['an expired card', { persistedAskUserQuestion: { ...PAYLOAD, expiresAt: '2020-01-01T00:00:00Z' } }],
    ['a card with no answers', { persistedAskUserQuestionAnswers: {} }],
    ['a reply that is not the latest', { isLastMessage: false }],
  ] as Array<[string, Partial<React.ComponentProps<typeof ChatResponse>>]>)(
    'keeps the card read-only for %s',
    (_name, props) => {
      renderReply(props);
      openRegenBar();

      expect(nextButton()).toBeNull();
    },
  );

  it('keeps the card read-only while the bar is open for another reply', () => {
    renderReply();
    openRegenBar('reply-0');

    expect(nextButton()).toBeNull();
  });
});
