import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { useChatStore } from '@/chat/store';
import type { MessagePart } from '@/chat/types';
import { splitPartsAtQuestion } from '@/chat/utils/tool-display';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: {} }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));

import { ChatResponse } from '../chat-response';

const card = {
  name: 'ask_user_question',
  userIntent: 'Pick the project to search',
  questions: [{ question: 'Which project should I look in?', options: [{ label: 'Apollo' }] }],
};
const answers = { q1: { questionUuid: 'q1', selectedOptionIds: ['o1'], userInputs: {} } };

const beforeNarration = { type: 'text', content: 'Checking what you asked for', settled: true };
const askCall = { type: 'tool_call', toolCallId: 'ask-1', toolName: 'ask_user_question', status: 'completed' };
const afterNarration = { type: 'text', content: 'Searching Apollo now', settled: true };
const afterTool = { type: 'tool_call', toolCallId: 'search-1', toolName: 'jira_search', status: 'completed' };
const finalText = { type: 'text', content: 'Two open issues.', isFinal: true };

const parts = (...p: object[]) => p as unknown as MessagePart[];

function renderRow(props: Partial<React.ComponentProps<typeof ChatResponse>> = {}) {
  return renderInTheme(
    <ChatResponse question="Find my issues" answer="Two open issues." messageId="msg-1" isLastMessage {...props} />,
  );
}

/** True when `a` comes before `b` in the document. */
function precedes(a: Node, b: Node): boolean {
  return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
}

const belowCard = () => screen.queryByTestId('activity-below-question-card');

beforeEach(() => {
  installBrowserShims();
});

afterEach(() => {
  cleanup();
  useChatStore.setState({ activeSlotId: null, slots: {} });
});

describe('splitPartsAtQuestion', () => {
  it('keeps the question call with the work before it and returns the rest after', () => {
    const split = splitPartsAtQuestion(parts(beforeNarration, askCall, afterNarration, afterTool));

    expect(split?.before).toEqual([beforeNarration, askCall]);
    expect(split?.after).toEqual([afterNarration, afterTool]);
  });

  it('splits at the first question call', () => {
    const secondAsk = { ...askCall, toolCallId: 'ask-2' };
    const split = splitPartsAtQuestion(parts(askCall, afterNarration, secondAsk));

    expect(split?.before).toEqual([askCall]);
    expect(split?.after).toEqual([afterNarration, secondAsk]);
  });

  it('returns null without a question call', () => {
    expect(splitPartsAtQuestion(parts(beforeNarration, afterTool))).toBeNull();
    expect(splitPartsAtQuestion(undefined)).toBeNull();
  });
});

describe('ChatResponse â€” question card order', () => {
  it('shows the work done after an answered question under the card', () => {
    renderRow({
      persistedAskUserQuestion: card as never,
      persistedAskUserQuestionAnswers: answers as never,
      persistedParts: parts(beforeNarration, askCall, afterNarration, afterTool, finalText),
    });

    const before = screen.getByText('Checking what you asked for');
    const question = screen.getByText('Pick the project to search');
    const after = screen.getByText('Searching Apollo now');
    const answer = screen.getByText('Two open issues.');

    expect(precedes(before, question)).toBe(true);
    expect(precedes(question, after)).toBe(true);
    expect(precedes(after, answer)).toBe(true);
    expect(belowCard()?.contains(after)).toBe(true);
    expect(belowCard()?.contains(before)).toBe(false);
  });

  it('adds no section under the card when nothing ran after the answer', () => {
    renderRow({
      persistedAskUserQuestion: card as never,
      persistedAskUserQuestionAnswers: answers as never,
      persistedParts: parts(beforeNarration, askCall, finalText),
    });

    expect(precedes(screen.getByText('Checking what you asked for'), screen.getByText('Pick the project to search'))).toBe(true);
    expect(belowCard()).toBeNull();
  });

  it('keeps every step above the card when the row has no question call', () => {
    renderRow({
      persistedAskUserQuestion: card as never,
      persistedAskUserQuestionAnswers: answers as never,
      persistedParts: parts(beforeNarration, afterNarration, afterTool, finalText),
    });

    expect(precedes(screen.getByText('Searching Apollo now'), screen.getByText('Pick the project to search'))).toBe(true);
    expect(belowCard()).toBeNull();
  });

  it('streams the resumed run under a submitted card', () => {
    const store = useChatStore.getState();
    const slotId = store.createSlot('conv-1');
    store.updateSlot(slotId, {
      pendingAskUserQuestion: {
        assistantMessageId: 'msg-1',
        payload: card as never,
        answers: answers as never,
        status: 'submitted',
      },
    });
    store.setActiveSlot(slotId);

    renderRow({
      answer: '',
      isStreaming: true,
      streamingContent: '',
      streamingParts: parts(beforeNarration, askCall, afterNarration, afterTool),
    });

    const question = screen.getByText('Pick the project to search');
    const after = screen.getByText('Searching Apollo now');
    expect(precedes(screen.getByText('Checking what you asked for'), question)).toBe(true);
    expect(precedes(question, after)).toBe(true);
    expect(belowCard()?.contains(after)).toBe(true);
  });

  it('keeps everything above a card that is still waiting for an answer', () => {
    const store = useChatStore.getState();
    const slotId = store.createSlot('conv-1');
    store.updateSlot(slotId, {
      pendingAskUserQuestion: {
        assistantMessageId: 'msg-1',
        payload: card as never,
        answers: {},
        status: 'pending',
      },
    });
    store.setActiveSlot(slotId);

    renderRow({ answer: '', persistedParts: parts(beforeNarration, askCall) });

    expect(precedes(screen.getByText('Checking what you asked for'), screen.getByText('Pick the project to search'))).toBe(true);
    expect(belowCard()).toBeNull();
  });
});
