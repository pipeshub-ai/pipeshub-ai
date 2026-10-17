import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { ToolApprovalDetails } from '../../../types';

const chat = vi.hoisted(() => ({
  streamMessageForSlot: vi.fn(),
  buildStreamChatRequestForSlot: vi.fn((slotId: string, query: string) => ({ slotId, query })),
  state: { activeSlotId: 'slot-1', slots: { 'slot-1': { isOwner: true as boolean | null, isStreaming: false } } },
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: {} }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: chat.streamMessageForSlot }));
vi.mock('../../../runtime', () => ({ buildStreamChatRequestForSlot: chat.buildStreamChatRequestForSlot }));
vi.mock('../../../store', () => ({
  useChatStore: Object.assign(
    (selector: (state: typeof chat.state) => unknown) => selector(chat.state),
    { getState: () => chat.state },
  ),
}));

import { ToolApprovalCard } from '../tool-approval-card';
import { ChatResponse } from '../chat-response';

function approval(overrides: Partial<ToolApprovalDetails> = {}): ToolApprovalDetails {
  return {
    approvalId: 'ap-1',
    instanceId: 'inst-1',
    serverName: 'Jira',
    toolName: 'create_issue',
    toolTitle: 'Create an issue',
    expiresAt: Date.now() + 15 * 60_000,
    arguments: { title: 'Login fails', labels: ['bug'] },
    ...overrides,
  };
}

beforeEach(() => {
  installBrowserShims();
  chat.state.slots['slot-1'].isOwner = true;
  chat.state.slots['slot-1'].isStreaming = false;
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe('ToolApprovalCard', () => {
  it('shows what would run, where, and for how long it can be answered', () => {
    renderInTheme(<ToolApprovalCard approval={approval()} isLatest />);

    expect(screen.getByText('Create an issue').getAttribute('title')).toBe('create_issue');
    expect(screen.getByText('Jira')).toBeTruthy();
    expect(screen.getByText(/^Needs your approval · expires in 15 min$/)).toBeTruthy();
    expect(screen.getByText('Login fails')).toBeTruthy();
    expect(screen.getByText('["bug"]')).toBeTruthy();
    expect(screen.queryByText('Deletes data')).toBeNull();
  });

  it('names a tool without a title readably', () => {
    renderInTheme(<ToolApprovalCard approval={approval({ toolTitle: null, toolName: 'createConfluencePage' })} isLatest />);
    expect(screen.getByText('Create Confluence Page')).toBeTruthy();
  });

  it('marks a tool that deletes data', () => {
    renderInTheme(<ToolApprovalCard approval={approval({ kind: 'destructive' })} isLatest />);
    expect(screen.getByText('Deletes data')).toBeTruthy();
  });

  it.each([
    ['Allow', 'allow'],
    ['Deny', 'deny'],
  ])('"%s" carries on the same reply with the answer', (button, decision) => {
    renderInTheme(<ToolApprovalCard approval={approval()} isLatest rowId="row-1" />);

    fireEvent.click(screen.getByRole('button', { name: button }));

    const query = `Tool approval: ${decision}`;
    expect(chat.buildStreamChatRequestForSlot).toHaveBeenCalledWith('slot-1', query, undefined, { approvalId: 'ap-1', decision });
    expect(chat.streamMessageForSlot).toHaveBeenCalledWith('slot-1', query, { slotId: 'slot-1', query }, { reuseRowId: 'row-1' });
  });

  it('"Don\'t ask again" goes with the answer', () => {
    renderInTheme(<ToolApprovalCard approval={approval()} isLatest rowId="row-1" />);

    fireEvent.click(screen.getByRole('checkbox', { name: "Don't ask again for this tool" }));
    fireEvent.click(screen.getByRole('button', { name: 'Deny' }));

    expect(chat.buildStreamChatRequestForSlot).toHaveBeenCalledWith(
      'slot-1', 'Tool approval: deny', undefined, { approvalId: 'ap-1', decision: 'deny', remember: true },
    );
  });

  it("has no \"Don't ask again\" on a toolset's card: an admin sets its rules", () => {
    renderInTheme(<ToolApprovalCard approval={approval({ serverName: 'Jira (Engineering)', canRemember: false })} isLatest rowId="row-1" />);

    expect(screen.queryByRole('checkbox')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Allow' }));
    expect(chat.buildStreamChatRequestForSlot).toHaveBeenCalledWith(
      'slot-1', 'Tool approval: allow', undefined, { approvalId: 'ap-1', decision: 'allow' },
    );
  });

  it('sends nothing while the chat is already answering', () => {
    chat.state.slots['slot-1'].isStreaming = true;
    renderInTheme(<ToolApprovalCard approval={approval()} isLatest rowId="row-1" />);

    fireEvent.click(screen.getByRole('button', { name: 'Allow' }));

    expect(chat.streamMessageForSlot).not.toHaveBeenCalled();
  });

  it('is one line, with no buttons, on an older reply or in a conversation shared with the viewer', () => {
    renderInTheme(<ToolApprovalCard approval={approval()} isLatest={false} />);
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByTestId('tool-approval-card').textContent).toBe("Create an issue on Jira wasn't approved, so it didn't run.");
    cleanup();

    chat.state.slots['slot-1'].isOwner = false;
    renderInTheme(<ToolApprovalCard approval={approval()} isLatest />);
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByTestId('tool-approval-card').textContent).toBe(
      "Create an issue on Jira is waiting for the conversation owner's approval.",
    );
  });

  it('says so once the request has expired', () => {
    vi.useFakeTimers();
    renderInTheme(<ToolApprovalCard approval={approval({ expiresAt: Date.now() + 1_000 })} isLatest />);
    expect(screen.getByRole('button', { name: 'Allow' })).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(1_001);
    });

    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByText("Create an issue on Jira didn't run: the request expired. Ask again if it's still needed.")).toBeTruthy();
  });

  it('shows the start of arguments too large to send, and says so', () => {
    renderInTheme(<ToolApprovalCard approval={approval({ arguments: null, argumentsPreview: '{"body": "aaaa…' })} isLatest />);
    expect(screen.getByText('{"body": "aaaa…')).toBeTruthy();
    expect(screen.getByText('Only the start of the details is shown: the full call is longer.')).toBeTruthy();
  });

  it('can show a long value in full before approving', () => {
    const body = `${'a'.repeat(300)}THE END`;
    renderInTheme(<ToolApprovalCard approval={approval({ arguments: { body } })} isLatest />);
    expect(screen.queryByText(body)).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Show all' }));

    expect(screen.getByText(body)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Show less' })).toBeTruthy();
  });
});

describe('ChatResponse with servers to sign in to again', () => {
  const parts = [
    { type: 'text' as const, content: 'Drive needs more permission.', isFinal: true },
    { type: 'mcp_sign_in' as const, servers: [{ instanceId: 'inst-drive', serverName: 'Drive', scopes: ['files.write'] }] },
  ];

  it('shows the sign-in card under the latest reply', () => {
    renderInTheme(<ChatResponse question="Q" answer="Drive needs more permission." persistedParts={parts} isLastMessage />);
    expect(screen.getByTestId('mcp-sign-in-card')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Sign in again' })).toBeTruthy();
  });

  it('not on an older reply, nor while it streams', () => {
    renderInTheme(<ChatResponse question="Q" answer="Drive needs more permission." persistedParts={parts} />);
    expect(screen.queryByTestId('mcp-sign-in-card')).toBeNull();
    cleanup();

    renderInTheme(<ChatResponse question="Q" answer="" isStreaming streamingParts={parts} isLastMessage />);
    expect(screen.queryByTestId('mcp-sign-in-card')).toBeNull();
  });
});

describe('ChatResponse with a call waiting for approval', () => {
  const parts = [
    { type: 'tool_call' as const, toolCallId: 'c1', toolName: 'mcp_jira_create_issue', status: 'awaiting_approval' as const, approval: approval() },
    { type: 'text' as const, content: 'Waiting for your approval to run create_issue on Jira.', isFinal: true },
  ];

  it('shows the card instead of the reply\'s waiting words', () => {
    renderInTheme(
      <ChatResponse
        question="Q"
        answer={'I will file it.\n\nWaiting for your approval to run create_issue on Jira.'}
        persistedParts={parts}
        isLastMessage
      />,
    );

    expect(screen.getByTestId('tool-approval-card')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Allow' })).toBeTruthy();
    expect(screen.getByText('I will file it.')).toBeTruthy();
    expect(screen.queryByText(/Waiting for your approval/)).toBeNull();
  });

  it.each([
    ['allowed', 'You allowed Create an issue'],
    ['declined', 'You declined Create an issue'],
    ['expired', "Create an issue didn't run: the request expired"],
  ] as const)('says above the answer what was done with an %s card', (approvalAnswer, line) => {
    const settled = [
      {
        type: 'tool_call' as const, toolCallId: 'c1', toolName: 'mcp_jira_create_issue',
        status: approvalAnswer === 'allowed' ? 'completed' as const : 'blocked' as const, approvalAnswer, approval: approval(),
      },
      { type: 'text' as const, content: 'Done.', isFinal: true },
    ];
    renderInTheme(<ChatResponse question="Q" answer="Done." persistedParts={settled} isLastMessage />);

    const receipts = screen.getByTestId('tool-approval-receipts');
    expect(receipts.textContent).toBe(`${line}Jira`);
    expect(screen.queryByTestId('tool-approval-card')).toBeNull();
  });

  it('drops the waiting words once the card is answered, while the reply carries on', () => {
    const resuming = [
      { type: 'tool_call' as const, toolCallId: 'c1', toolName: 'mcp_jira_create_issue', status: 'running' as const, approved: true, approvalAnswer: 'allowed' as const, approval: approval() },
      { type: 'text' as const, content: 'Waiting for your approval to run create_issue on Jira.', isFinal: true },
    ];
    renderInTheme(
      <ChatResponse
        question="Q"
        answer="Waiting for your approval to run create_issue on Jira."
        isStreaming
        streamingContent=""
        streamingParts={resuming}
        isLastMessage
      />,
    );

    expect(screen.queryByText(/Waiting for your approval/)).toBeNull();
    expect(screen.getByTestId('tool-approval-receipts').textContent).toContain('You allowed Create an issue');
  });

  it('shows the card under a question the reply asked and the person answered', () => {
    const question = {
      name: 'ask_user_question',
      questions: [{ uuid: 'q1', question: 'Short summary or detailed guide?', multiSelect: false, options: [{ id: 'short', label: 'Short summary', isUserInput: false }] }],
    };
    const parts = [
      { type: 'tool_call' as const, toolCallId: 'a1', toolName: 'internaltools__ask_user_question', status: 'completed' as const },
      { type: 'tool_call' as const, toolCallId: 'c1', toolName: 'mcp_jira_create_issue', status: 'awaiting_approval' as const, approval: approval() },
      { type: 'text' as const, content: 'Waiting for your approval to run create_issue on Jira.', isFinal: true },
    ];
    renderInTheme(
      <ChatResponse
        question="Q"
        answer="Waiting for your approval to run create_issue on Jira."
        persistedParts={parts}
        persistedAskUserQuestion={question as never}
        persistedAskUserQuestionAnswers={{ q1: { questionUuid: 'q1', selectedOptionIds: ['short'], userInputs: {} } } as never}
        isLastMessage
      />,
    );

    expect(screen.getByTestId('tool-approval-card')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Allow' })).toBeTruthy();
  });

  it('says when an allowed call was stopped before it ran', () => {
    const stopped = [
      { type: 'tool_call' as const, toolCallId: 'c1', toolName: 'mcp_jira_create_issue', status: 'blocked' as const, approvalAnswer: 'allowed' as const, approval: approval() },
      { type: 'text' as const, content: 'It was blocked.', isFinal: true },
    ];
    renderInTheme(<ChatResponse question="Q" answer="It was blocked." persistedParts={stopped} isLastMessage />);

    expect(screen.getByTestId('tool-approval-receipts').textContent).toBe("You allowed Create an issue, but it didn't runJira");
  });

  it('offers no Regenerate on a reply that carried on from an answered card', () => {
    const declined = [
      { type: 'tool_call' as const, toolCallId: 'c1', toolName: 'mcp_jira_create_issue', status: 'blocked' as const, approvalAnswer: 'declined' as const },
      { type: 'text' as const, content: 'OK, I left it.', isFinal: true },
    ];
    renderInTheme(<ChatResponse question="Q" answer="OK, I left it." persistedParts={declined} isLastMessage messageId="m-1" />);
    expect(screen.queryByRole('button', { name: 'Regenerate' })).toBeNull();
  });

  it('offers no Regenerate on a reply that ran an approved action', () => {
    const ran = [
      { type: 'tool_call' as const, toolCallId: 'approved_1', toolName: 'mcp_jira_create_issue', status: 'completed' as const, approved: true },
      { type: 'text' as const, content: 'Created it.', isFinal: true },
    ];
    renderInTheme(<ChatResponse question="Q" answer="Created it." persistedParts={ran} isLastMessage messageId="m-1" />);
    expect(screen.queryByRole('button', { name: 'Regenerate' })).toBeNull();
    cleanup();

    renderInTheme(<ChatResponse question="Q" answer="Plain." persistedParts={[{ type: 'text', content: 'Plain.', isFinal: true }]} isLastMessage messageId="m-2" />);
    expect(screen.getByRole('button', { name: 'Regenerate' })).toBeTruthy();
  });

  it('shows no card while the reply is still streaming', () => {
    renderInTheme(<ChatResponse question="Q" answer="" isStreaming streamingParts={parts} isLastMessage />);
    expect(screen.queryByTestId('tool-approval-card')).toBeNull();
  });
});
