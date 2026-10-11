import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { ToolApprovalDetails } from '../../../types';

const chat = vi.hoisted(() => ({
  streamMessageForSlot: vi.fn(),
  buildStreamChatRequestForSlot: vi.fn((slotId: string, query: string) => ({ slotId, query })),
  state: { activeSlotId: 'slot-1', slots: { 'slot-1': { isOwner: true as boolean | null } } },
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
    canAlwaysAllow: true,
    expiresAt: Date.now() + 15 * 60_000,
    arguments: { title: 'Login fails', labels: ['bug'] },
    ...overrides,
  };
}

beforeEach(() => {
  installBrowserShims();
  chat.state.slots['slot-1'].isOwner = true;
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe('ToolApprovalCard', () => {
  it('shows what would run, and where', () => {
    renderInTheme(<ToolApprovalCard approval={approval()} isLatest />);

    expect(screen.getByText('Run Create an issue on Jira')).toBeTruthy();
    expect(screen.getByText('(create_issue)')).toBeTruthy();
    expect(screen.getByText('Login fails')).toBeTruthy();
    expect(screen.getByText('["bug"]')).toBeTruthy();
    expect(screen.queryByText('Deletes data')).toBeNull();
  });

  it('marks a tool that deletes data', () => {
    renderInTheme(<ToolApprovalCard approval={approval({ kind: 'destructive' })} isLatest />);
    expect(screen.getByText('Deletes data')).toBeTruthy();
  });

  it.each([
    ['Allow once', 'allow_once', 'Allow once: Create an issue on Jira'],
    ['Allow for this chat', 'allow_chat', 'Allow for this chat: Create an issue on Jira'],
    ['Always allow', 'always', 'Always allow: Create an issue on Jira'],
    ['Deny', 'deny', 'Deny: Create an issue on Jira'],
  ])('"%s" sends the choice as the next message, once', (button, decision, text) => {
    renderInTheme(<ToolApprovalCard approval={approval()} isLatest />);

    fireEvent.click(screen.getByRole('button', { name: button }));

    expect(chat.buildStreamChatRequestForSlot).toHaveBeenCalledWith('slot-1', text, undefined, { approvalId: 'ap-1', decision });
    expect(chat.streamMessageForSlot).toHaveBeenCalledWith('slot-1', text, { slotId: 'slot-1', query: text });
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('offers Always allow only to someone who can set the rule', () => {
    renderInTheme(<ToolApprovalCard approval={approval({ canAlwaysAllow: false })} isLatest />);

    expect(screen.getByRole('button', { name: 'Allow for this chat' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Always allow' })).toBeNull();
  });

  it('when the company asks every time, offers only this call', () => {
    renderInTheme(<ToolApprovalCard approval={approval({ companyAlwaysAsk: true })} isLatest />);

    expect(screen.getByText('Your organization asks every time before this tool runs.')).toBeTruthy();
    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual(['Allow once', 'Deny']);
  });

  it('has no buttons on an older reply, or in a conversation shared with the viewer', () => {
    renderInTheme(<ToolApprovalCard approval={approval()} isLatest={false} />);
    expect(screen.queryByRole('button')).toBeNull();
    cleanup();

    chat.state.slots['slot-1'].isOwner = false;
    renderInTheme(<ToolApprovalCard approval={approval()} isLatest />);
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('says so once the request has expired', () => {
    vi.useFakeTimers();
    renderInTheme(<ToolApprovalCard approval={approval({ expiresAt: Date.now() + 1_000 })} isLatest />);
    expect(screen.getByRole('button', { name: 'Allow once' })).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(1_001);
    });

    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByText("This request expired. Ask again if it's still needed.")).toBeTruthy();
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

  it('shows the card under the answer', () => {
    renderInTheme(<ChatResponse question="Q" answer="Waiting for your approval to run create_issue on Jira." persistedParts={parts} isLastMessage />);

    expect(screen.getByTestId('tool-approval-card')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Allow once' })).toBeTruthy();
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
