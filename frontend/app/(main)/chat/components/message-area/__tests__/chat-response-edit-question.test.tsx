import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { useCommandStore } from '@/lib/store/command-store';
import { useChatStore } from '@/chat/store';
import type { MessagePart } from '@/chat/types';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: {} }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));

import { ChatResponse } from '../chat-response';

const editButton = () => screen.queryByRole('button', { name: 'Edit' });

function renderAnswer(props: Partial<React.ComponentProps<typeof ChatResponse>> = {}) {
  return renderInTheme(
    <ChatResponse question="What changed?" answer="Two things." messageId="msg-1" isLastMessage {...props} />,
  );
}

beforeEach(() => {
  installBrowserShims();
});

afterEach(() => {
  cleanup();
  useCommandStore.setState({ handlers: {} } as never);
});

describe('ChatResponse — editing the question', () => {
  it('offers the edit on the newest question', () => {
    renderAnswer();

    expect(editButton()).toBeTruthy();
  });

  it('does not offer it on an earlier question', () => {
    renderAnswer({ isLastMessage: false });

    expect(editButton()).toBeNull();
  });

  it('does not offer it while the answer is streaming', () => {
    renderAnswer({ isStreaming: true });

    expect(editButton()).toBeNull();
  });

  it('does not offer it on an answer without a stored message to replace', () => {
    renderAnswer({ messageId: undefined });

    expect(editButton()).toBeNull();
  });

  it('does not offer it when the reply ran an action the user approved', () => {
    const parts = [{ type: 'tool_call', toolCallId: 't1', toolName: 'create_issue', approved: true }] as unknown as MessagePart[];

    renderAnswer({ persistedParts: parts });

    expect(editButton()).toBeNull();
  });

  it('does not offer it on the answer to a tool approval', () => {
    renderAnswer({ question: 'Tool approval: allow once' });

    expect(editButton()).toBeNull();
  });

  const card = {
    name: 'ask_user_question',
    questions: [{ question: 'What do you enjoy?', options: [{ label: 'Pop' }] }],
  };
  const answers = { q1: { questionUuid: 'q1', selectedOptionIds: ['o1'], userInputs: {} } };

  it('offers it on a turn whose question card was answered', () => {
    renderAnswer({ persistedAskUserQuestion: card as never, persistedAskUserQuestionAnswers: answers as never });

    expect(editButton()).toBeTruthy();
  });

  it('does not offer it while the question card is still waiting for an answer', () => {
    renderAnswer({ persistedAskUserQuestion: card as never, persistedAskUserQuestionAnswers: {} });

    expect(editButton()).toBeNull();
  });

  it('does not offer it to someone a chat was shared with', () => {
    const store = useChatStore.getState();
    const slotId = store.createSlot('conv-shared');
    store.updateSlot(slotId, { isOwner: false });
    store.setActiveSlot(slotId);

    renderAnswer();

    expect(editButton()).toBeNull();
    useChatStore.setState({ activeSlotId: null, slots: {} });
  });

  it('opens the editor with the question and the filters it was asked with', () => {
    const received: unknown[] = [];
    useCommandStore.getState().register('showEditQuery', (payload) => received.push(payload));
    const appliedFilters = { apps: [{ id: 'app-drive', name: 'Drive' }], kb: [] };

    renderAnswer({ appliedFilters: appliedFilters as never });
    fireEvent.click(editButton() as HTMLElement);

    expect(received).toEqual([{ messageId: 'msg-1', text: 'What changed?', appliedFilters }]);
  });

  it('hands the files of the question to the editor', () => {
    const received: unknown[] = [];
    useCommandStore.getState().register('showEditQuery', (payload) => received.push(payload));
    const attachments = [{ recordId: 'rec-a', recordName: 'a.pdf', mimeType: 'application/pdf', extension: 'pdf', virtualRecordId: 'v-a' }];

    renderAnswer({ attachments });
    fireEvent.click(editButton() as HTMLElement);

    expect(received).toEqual([{ messageId: 'msg-1', text: 'What changed?', appliedFilters: undefined, attachments }]);
  });
});
