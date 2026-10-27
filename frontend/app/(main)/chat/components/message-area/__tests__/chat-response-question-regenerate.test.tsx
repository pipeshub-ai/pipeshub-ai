import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { useChatStore } from '@/chat/store';
import type { MessagePart } from '@/chat/types';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: {} }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));
vi.mock('../../../runtime', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../runtime')>()),
  buildStreamChatRequestForSlot: vi.fn(() => ({ query: 'resume' })),
}));

import { ChatResponse } from '../chat-response';
import { streamMessageForSlot } from '../../../streaming';

const card = {
  name: 'ask_user_question',
  userIntent: 'Pick where to look',
  questions: [
    { uuid: 'q-project', question: 'Which project?', options: [{ id: 'apollo', label: 'Apollo' }, { id: 'zeus', label: 'Zeus' }] },
    { uuid: 'q-state', question: 'Which issues?', options: [{ id: 'open', label: 'Open' }, { id: 'closed', label: 'Closed' }] },
  ],
};
const zeusClosed = {
  'q-project': { questionUuid: 'q-project', selectedOptionIds: ['zeus'], userInputs: {} },
  'q-state': { questionUuid: 'q-state', selectedOptionIds: ['closed'], userInputs: {} },
};

const beforeNarration = { type: 'text', content: 'Reading your request', settled: true };
const askCall = { type: 'tool_call', toolCallId: 'ask-1', toolName: 'ask_user_question', status: 'completed' };
const afterNarration = { type: 'text', content: 'Searching Zeus for closed issues', settled: true };
const afterTool = { type: 'tool_call', toolCallId: 'search-1', toolName: 'jira_search', status: 'completed' };
const finalText = { type: 'text', content: 'Three closed issues.', isFinal: true };
const parts = (...p: object[]) => p as unknown as MessagePart[];

let slotId = '';
const pending = () => useChatStore.getState().slots[slotId]?.pendingAskUserQuestion;

function Row(props: Partial<React.ComponentProps<typeof ChatResponse>>) {
  return <ChatResponse question="Find my issues" answer="" messageId="msg-1" isLastMessage {...props} />;
}

function precedes(a: Node, b: Node): boolean {
  return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
}

const belowCard = () => screen.queryByTestId('activity-below-question-card');
const click = (name: string) => fireEvent.click(screen.getByRole('button', { name }));
const pick = (name: string) => fireEvent.click(screen.getByRole('radio', { name }));

beforeEach(() => {
  installBrowserShims();
  vi.mocked(streamMessageForSlot).mockClear();
  const store = useChatStore.getState();
  slotId = store.createSlot('conv-1');
  store.setActiveSlot(slotId);
});

afterEach(() => {
  cleanup();
  useChatStore.setState({ activeSlotId: null, slots: {}, regenAnswers: null, regenTargetMessageId: null });
});

describe('ChatResponse — answering the card, and the work after it', () => {
  it('lets the user pick, change and submit answers, then shows the resumed run under the card', () => {
    useChatStore.getState().updateSlot(slotId, {
      pendingAskUserQuestion: { assistantMessageId: 'msg-1', payload: card as never, answers: {}, status: 'pending' },
    });
    const view = renderInTheme(<Row persistedParts={parts(beforeNarration, askCall)} />);

    expect(precedes(screen.getByText('Reading your request'), screen.getByText('Pick where to look'))).toBe(true);
    expect(belowCard()).toBeNull();

    pick('Apollo');
    expect(pending()?.answers['q-project']?.selectedOptionIds).toEqual(['apollo']);
    pick('Zeus');
    expect(pending()?.answers['q-project']?.selectedOptionIds).toEqual(['zeus']);

    click('Next');
    pick('Open');
    click('Back');
    expect(screen.getByRole('radio', { name: 'Zeus' }).getAttribute('aria-checked')).toBe('true');
    click('Next');
    pick('Closed');
    expect(pending()?.answers['q-state']?.selectedOptionIds).toEqual(['closed']);

    click('Submit');
    expect(pending()?.status).toBe('submitted');
    expect(streamMessageForSlot).toHaveBeenCalledTimes(1);
    const [sid, message, , options] = vi.mocked(streamMessageForSlot).mock.calls[0];
    expect(sid).toBe(slotId);
    expect(message).toContain('Zeus');
    expect(message).toContain('Closed');
    expect(message).not.toContain('Apollo');
    expect(options).toEqual({ resumeAskUserQuestion: true });

    view.rerender(
      <Row isStreaming streamingContent="" streamingParts={parts(beforeNarration, askCall, afterNarration, afterTool)} />,
    );
    const shownCard = screen.getByText('Pick where to look');
    const after = screen.getByText('Searching Zeus for closed issues');
    expect(precedes(screen.getByText('Reading your request'), shownCard)).toBe(true);
    expect(precedes(shownCard, after)).toBe(true);
    expect(belowCard()?.contains(after)).toBe(true);
  });

  it('cannot submit the card twice once its answers are sent', () => {
    useChatStore.getState().updateSlot(slotId, {
      pendingAskUserQuestion: { assistantMessageId: 'msg-1', payload: card as never, answers: {}, status: 'pending' },
    });
    renderInTheme(<Row persistedParts={parts(beforeNarration, askCall)} />);

    pick('Zeus');
    click('Next');
    pick('Open');
    click('Submit');

    expect(screen.queryByRole('button', { name: 'Submit' })).toBeNull();
    expect(screen.queryByRole('radio', { name: 'Zeus' })).toBeNull();
    expect(streamMessageForSlot).toHaveBeenCalledTimes(1);
  });

  it('keeps the order once the answered turn is saved and reloaded', () => {
    renderInTheme(
      <Row
        answer="Three closed issues."
        persistedAskUserQuestion={card as never}
        persistedAskUserQuestionAnswers={zeusClosed as never}
        persistedParts={parts(beforeNarration, askCall, afterNarration, afterTool, finalText)}
      />,
    );

    const shownCard = screen.getByText('Pick where to look');
    const after = screen.getByText('Searching Zeus for closed issues');
    expect(precedes(screen.getByText('Reading your request'), shownCard)).toBe(true);
    expect(precedes(shownCard, after)).toBe(true);
    expect(precedes(after, screen.getByText('Three closed issues.'))).toBe(true);
  });

  it('streams a regenerate with changed answers under the card it is running with', () => {
    useChatStore.setState({
      regenAnswers: { messageId: 'msg-1', payload: card as never, message: 'User selections: …', answers: zeusClosed as never },
    });

    renderInTheme(
      <Row
        isStreaming
        streamingContent=""
        streamingParts={parts(afterNarration, afterTool)}
        persistedAskUserQuestion={card as never}
        persistedAskUserQuestionAnswers={{ 'q-project': { questionUuid: 'q-project', selectedOptionIds: ['apollo'], userInputs: {} } } as never}
      />,
    );

    const shownCard = screen.getByText('Pick where to look');
    const after = screen.getByText('Searching Zeus for closed issues');
    expect(precedes(shownCard, after)).toBe(true);
    expect(belowCard()?.contains(after)).toBe(true);
  });
});
