import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: {} }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));

import { ChatResponse } from '../chat-response';
import { RunErrorCard } from '../run-error-card';
import { AnswerSlotContext } from '../answer-slot-scope';
import { useChatStore } from '../../../store';

const MESSAGE = "This agent requires the following MCP servers to be set up — not authenticated: 'GitHub'.";
const STREAM_ERROR = {
  code: 'mcp_server_config_missing',
  details: {
    agentId: 'agent-1',
    serviceAccount: false,
    servers: [{ instanceId: 'inst-gh', name: 'GitHub', problem: 'not_connected', authMode: 'oauth' }],
  },
};

beforeEach(() => {
  installBrowserShims();
});

afterEach(() => {
  cleanup();
});

describe('ChatResponse for a run stopped on MCP servers', () => {
  it('shows the Connect card instead of the error text', () => {
    renderInTheme(<ChatResponse question="Q" answer={MESSAGE} streamError={STREAM_ERROR} />);

    expect(screen.getByTestId('mcp-connect-required')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Connect' })).toBeTruthy();
    expect(screen.queryByText(MESSAGE)).toBeNull();
  });

  it('keeps the text for any other error', () => {
    renderInTheme(
      <ChatResponse question="Q" answer="The model is not responding." streamError={{ code: 'stream_error' }} />,
    );

    expect(screen.queryByTestId('mcp-connect-required')).toBeNull();
    expect(screen.getByText('The model is not responding.')).toBeTruthy();
  });
});

describe('ChatResponse retry on a failed answer', () => {
  it('hands back the whole turn, attachments and filters included', async () => {
    const onRetry = vi.fn();
    const attachments = [
      { recordId: 'r1', recordName: 'plan.pdf', mimeType: 'application/pdf', extension: 'pdf', virtualRecordId: 'v1' },
    ];
    const appliedFilters = { apps: [{ id: 'app-1', name: 'Drive', nodeType: '', connector: '' }], kb: [] };
    renderInTheme(
      <ChatResponse
        question=""
        answer="The model is not responding."
        failed
        isLastMessage
        attachments={attachments}
        appliedFilters={appliedFilters}
        onRetry={onRetry}
      />,
    );

    screen.getByTestId('answer-failed-retry').click();

    expect(onRetry).toHaveBeenCalledWith({ question: '', attachments, collections: undefined, appliedFilters });
  });
});

describe('Try again in place of the failed copy', () => {
  const failedTurn = (questionId?: string) => [
    { id: 'q0', role: 'user' as const, content: [{ type: 'text' as const, text: 'Earlier' }] },
    { id: 'a0', role: 'assistant' as const, content: [{ type: 'text' as const, text: 'Answered' }] },
    { ...(questionId ? { id: questionId } : {}), role: 'user' as const, content: [{ type: 'text' as const, text: 'How many?' }] },
    { id: 'p1', role: 'assistant' as const, content: [{ type: 'text' as const, text: 'The model is not responding.' }], metadata: { custom: { failed: true } } },
  ];
  const openSlot = (convId: string, patch: Record<string, unknown>) => {
    const id = useChatStore.getState().createSlot(convId);
    useChatStore.getState().updateSlot(id, patch);
    return id;
  };
  const retryIn = (slotId: string | null, onRetry: () => void) => {
    const row = <ChatResponse question="How many?" answer="The model is not responding." failed isLastMessage rowId="p1" onRetry={onRetry} />;
    renderInTheme(slotId ? <AnswerSlotContext.Provider value={slotId}>{row}</AnswerSlotContext.Provider> : row);
    fireEvent.click(screen.getByTestId('answer-failed-retry'));
  };
  const textsOf = (slotId: string) =>
    useChatStore.getState().slots[slotId].messages.map((m) => (m.content as Array<{ text: string }>)[0].text);

  beforeEach(() => {
    useChatStore.setState({ slots: {}, activeSlotId: null });
  });

  it("drops the failed question and its reply from the answer's own slot before asking again, so the question shows once", () => {
    const active = openSlot('c-active', { messages: failedTurn() });
    const beside = openSlot('c-beside', { messages: failedTurn() });
    useChatStore.setState({ activeSlotId: active });
    let rowsWhenAsked: string[] = [];
    const onRetry = vi.fn(() => {
      rowsWhenAsked = textsOf(beside);
    });

    retryIn(beside, onRetry);

    expect(onRetry).toHaveBeenCalledWith(expect.objectContaining({ question: 'How many?' }));
    expect(rowsWhenAsked, 'gone before the question is asked again').toEqual(['Earlier', 'Answered']);
    expect(textsOf(active), 'another slot is untouched').toHaveLength(4);
  });

  it('does so in the active chat when no slot is provided', () => {
    const active = openSlot('c-active', { messages: failedTurn() });
    useChatStore.setState({ activeSlotId: active });
    retryIn(null, vi.fn());
    expect(textsOf(active)).toEqual(['Earlier', 'Answered']);
  });

  it('keeps a question the server stored, and keeps the copy while the chat is busy (the send may not go)', () => {
    const stored = openSlot('c-stored', { messages: failedTurn('q-stored') });
    retryIn(stored, vi.fn());
    expect(textsOf(stored)).toHaveLength(4);
    cleanup();
    const busy = openSlot('c-busy', { messages: failedTurn(), remoteRunInProgress: true });
    retryIn(busy, vi.fn());
    expect(textsOf(busy)).toHaveLength(4);
  });
});

describe('a refusal only an admin can lift', () => {
  const REFUSAL = 'This request was refused, so it did not run. Ask an admin for access.';

  it("shows the server's text, with its guidance, and no Try again", () => {
    renderInTheme(<ChatResponse question="Q" answer={REFUSAL} failed isLastMessage onRetry={vi.fn()} streamError={{ code: 'ai_budget_exceeded' }} />);
    expect(screen.getByTestId('answer-failed').textContent).toContain(REFUSAL);
    expect(screen.queryByTestId('answer-failed-retry')).toBeNull();
  });

  it('any other failed run keeps Try again', () => {
    renderInTheme(<ChatResponse question="Q" answer="The AI model is not responding." failed isLastMessage onRetry={vi.fn()} streamError={{ code: 'stream_error' }} />);
    expect(screen.getByTestId('answer-failed-retry')).toBeTruthy();
  });
});

describe('ChatResponse regenerate', () => {
  it('offers Regenerate only to someone who can send in this chat', () => {
    const { unmount } = renderInTheme(<ChatResponse question="Q" answer="An answer." isLastMessage messageId="m1" />);
    expect(screen.getByRole('button', { name: 'Regenerate' })).toBeTruthy();
    unmount();

    renderInTheme(<ChatResponse question="Q" answer="An answer." isLastMessage messageId="m1" canSend={false} />);
    expect(screen.queryByRole('button', { name: 'Regenerate' })).toBeNull();
  });
});

describe('RunErrorCard tones', () => {
  it('draws a failed run in the error tone and a stopped one in the neutral tone', () => {
    renderInTheme(<RunErrorCard tone="error" icon="error" title="The server could not answer." testId="failed-card" />);
    const failed = screen.getByTestId('failed-card');
    expect(failed.style.border).toBe('1px solid var(--red-6)');
    expect(failed.style.background).toBe('var(--red-2)');
    cleanup();

    renderInTheme(<ChatResponse question="Q" answer="" status="stopped" />);
    const stopped = screen.getByTestId('chat-stopped-card');
    expect(stopped.style.border).toBe('1px solid var(--border-default)');
    expect(stopped.style.background).toBe('var(--surface-raised)');
  });
});

describe('ChatResponse for a stopped run', () => {
  it('says the person stopped it, keeps the words it wrote above, and offers no toolbar when nothing was written', () => {
    renderInTheme(<ChatResponse question="Q" answer="The first part." status="stopped" />);
    const card = screen.getByTestId('chat-stopped-card');
    expect(card.getAttribute('role')).toBe('status');
    expect(card.textContent).toContain('You stopped this response');
    expect(card.textContent).toContain('What was written before you stopped is kept above.');
    expect(screen.queryByTestId('chat-stopped-marker')).toBeNull();
    cleanup();

    renderInTheme(<ChatResponse question="Q" answer="" status="stopped" messageId="m1" />);
    const empty = screen.getByTestId('chat-stopped-card');
    expect(empty.textContent).toBe('You stopped this response');
    expect(screen.queryByTestId('message-actions')).toBeNull();
  });

  it('stopped before its first word: no trail, and no word about steps that never happened', () => {
    const parts = [{ type: 'text', content: '' }, { type: 'text', content: '', isFinal: true }];
    renderInTheme(<ChatResponse question="Q" answer="" status="stopped" persistedParts={parts as never} />);
    expect(screen.getByTestId('chat-stopped-card').textContent).toBe('You stopped this response');
    expect(screen.queryByTestId('chat-activity-card')).toBeNull();
  });

  it('points at the trail only when a trail with steps is shown, else at the kept words, else says nothing more', () => {
    const tool = { type: 'tool_call', toolCallId: 't1', toolName: 'jira_search', status: 'completed' };
    renderInTheme(
      <ChatResponse question="Q" answer="The first part." status="stopped" persistedParts={[tool] as never} />,
    );
    expect(screen.getByTestId('chat-stopped-card').textContent).toContain(
      'The steps that finished are still in the trail above.',
    );
    cleanup();

    renderInTheme(
      <ChatResponse
        question="Q"
        answer="The first part."
        status="stopped"
        persistedParts={[{ type: 'text', content: 'The first part.', isFinal: true }] as never}
      />,
    );
    const textOnly = screen.getByTestId('chat-stopped-card');
    expect(textOnly.textContent).toContain('What was written before you stopped is kept above.');
    expect(textOnly.textContent).not.toContain('trail');
    cleanup();

    renderInTheme(<ChatResponse question="Q" answer="" status="stopped" persistedParts={[]} />);
    expect(screen.getByTestId('chat-stopped-card').textContent).toBe('You stopped this response');
  });

  it('offers Try again where a retry is allowed', () => {
    const onRetry = vi.fn();
    renderInTheme(<ChatResponse question="Q" answer="" status="stopped" isLastMessage onRetry={onRetry} />);
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
