/**
 * What a reply hands its copy / read-aloud actions: the reply's own answer,
 * or the question card's text while that card still waits for an answer.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { AskUserQuestionPayload } from '../../../types';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: {} }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));
const actionsContent = vi.hoisted(() => ({ value: undefined as string | undefined }));
vi.mock('../message-actions', () => ({
  MessageActions: ({ content }: { content: string }) => {
    actionsContent.value = content;
    return null;
  },
}));

import { ChatResponse } from '../chat-response';
import { useChatStore } from '../../../store';

const PAYLOAD: AskUserQuestionPayload = {
  name: 'ask_user_question',
  questions: [
    {
      uuid: 'q1',
      question: 'What should the essay be about?',
      multiSelect: false,
      options: [
        { id: 'edu', label: 'Education', isUserInput: false },
        { id: 'tech', label: 'Technology', isUserInput: false },
      ],
    },
  ],
};
const ESSAY = '# Technology\n\nTechnology has changed **how we work**.';

function setCard(status: 'pending' | 'submitted') {
  useChatStore.setState({
    activeSlotId: 'slot-1',
    slots: {
      'slot-1': {
        pendingAskUserQuestion: {
          assistantMessageId: 'reply-1',
          payload: PAYLOAD,
          answers: status === 'pending' ? {} : { q1: { questionUuid: 'q1', selectedOptionIds: ['tech'], userInputs: {} } },
          status,
        },
      } as never,
    },
  });
}

beforeEach(() => {
  installBrowserShims();
  actionsContent.value = undefined;
});

afterEach(() => {
  cleanup();
  useChatStore.setState({ activeSlotId: null, slots: {} });
});

describe('ChatResponse, content handed to the copy actions', () => {
  it('is the answer written after the question card was answered', () => {
    setCard('submitted');
    renderInTheme(<ChatResponse question="write an essay" answer={ESSAY} messageId="reply-1" isLastMessage />);

    expect(actionsContent.value).toBe(ESSAY);
  });

  it('is the question card while it still waits for an answer', () => {
    setCard('pending');
    renderInTheme(<ChatResponse question="write an essay" answer="" messageId="reply-1" isLastMessage />);

    expect(actionsContent.value).toContain('What should the essay be about?');
    expect(actionsContent.value).toContain('Technology');
  });
});
