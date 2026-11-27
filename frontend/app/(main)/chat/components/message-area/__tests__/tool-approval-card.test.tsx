import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { MessagePart, ToolApprovalDetails } from '../../../types';

const chat = vi.hoisted(() => ({
  streamMessageForSlot: vi.fn(),
  buildStreamChatRequestForSlot: vi.fn((slotId: string, query: string) => ({ slotId, query })),
  state: {
    activeSlotId: 'slot-1',
    slots: { 'slot-1': { isOwner: true as boolean | null, isStreaming: false } },
    // The harness's message actions read the configured models (the model a run used).
    settings: { availableModels: [] },
  },
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
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

import { ToolApprovalCard, ToolApprovalReceipts } from '../tool-approval-card';
import { settledToolApprovals } from '../../../tool-approval';
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
    expect(screen.getByText('bug')).toBeTruthy();
    expect(screen.queryByText("Can't be undone")).toBeNull();
  });

  it('names a tool without a title readably', () => {
    renderInTheme(<ToolApprovalCard approval={approval({ toolTitle: null, toolName: 'createConfluencePage' })} isLatest />);
    expect(screen.getByText('Create Confluence Page')).toBeTruthy();
  });

  it('marks a tool that deletes data', () => {
    renderInTheme(<ToolApprovalCard approval={approval({ kind: 'destructive' })} isLatest />);
    expect(screen.getByText("Can't be undone")).toBeTruthy();
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

  it('is answered by whoever the extension names instead of the owner, and is one line naming them for everyone else', () => {
    chat.state.slots['slot-1'].isOwner = false;
    renderInTheme(<ToolApprovalCard approval={approval()} isLatest readOnlyFor={null} />);
    expect(screen.getByRole('button', { name: 'Allow' })).toBeTruthy();
    cleanup();

    chat.state.slots['slot-1'].isOwner = true;
    renderInTheme(<ToolApprovalCard approval={approval()} isLatest readOnlyFor={{ name: 'Dana' }} />);
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByTestId('tool-approval-card').textContent).toBe("Create an issue on Jira is waiting for Dana's approval.");
    cleanup();

    renderInTheme(<ToolApprovalCard approval={approval()} isLatest readOnlyFor={{ name: null }} />);
    expect(screen.getByTestId('tool-approval-card').textContent).toBe("Create an issue on Jira is waiting for Former member's approval.");
    cleanup();

    // An older reply's card was passed over, whoever it was put to.
    renderInTheme(<ToolApprovalCard approval={approval()} isLatest={false} readOnlyFor={{ name: 'Dana' }} />);
    expect(screen.getByTestId('tool-approval-card').textContent).toBe("Create an issue on Jira wasn't approved, so it didn't run.");
  });

  it('can be read but not answered while it is held, and says why', () => {
    renderInTheme(<ToolApprovalCard approval={approval()} isLatest rowId="row-1" held="You can answer once Dana's question is done." />);
    expect(screen.getByText('Login fails')).toBeTruthy();
    for (const name of ['Allow', 'Deny']) {
      const button = screen.getByRole('button', { name }) as HTMLButtonElement;
      expect(button.disabled).toBe(true);
      fireEvent.click(button);
    }
    expect(chat.streamMessageForSlot).not.toHaveBeenCalled();
    const why = screen.getByTestId('tool-approval-held');
    expect(why.textContent).toBe("You can answer once Dana's question is done.");
    expect(why.getAttribute('role')).toBe('status');
    cleanup();

    renderInTheme(<ToolApprovalCard approval={approval()} isLatest rowId="row-1" />);
    expect(screen.queryByTestId('tool-approval-held')).toBeNull();
    expect((screen.getByRole('button', { name: 'Allow' }) as HTMLButtonElement).disabled).toBe(false);
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

  // A card its asker's next question closed read "expired" once its time was up.
  it('closed by its asker\'s next question before it expired, it says so for good and never "expired"', () => {
    vi.useFakeTimers();
    const expiresAt = Date.now() + 1_000;
    renderInTheme(<ToolApprovalCard approval={approval({ expiresAt })} isLatest={false} closedAt={expiresAt - 500} />);
    const line = () => screen.getByTestId('tool-approval-card').textContent;
    expect(line()).toBe("You asked something else, so Create an issue on Jira didn't run.");
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(line()).toBe("You asked something else, so Create an issue on Jira didn't run.");
    cleanup();

    const webPage = { instanceId: '', serverName: 'Open a web page', toolName: 'dynamic__fetch_url', toolTitle: 'Open a web page' };
    renderInTheme(<ToolApprovalCard approval={approval({ ...webPage, expiresAt })} isLatest={false} closedAt={expiresAt - 500} />);
    expect(line()).toBe("You asked something else, so Open a web page didn't run.");
    cleanup();

    // Someone else's card, put to them: not "you".
    renderInTheme(<ToolApprovalCard approval={approval({ expiresAt })} isLatest={false} closedAt={expiresAt - 500} readOnlyFor={{ name: 'Dana' }} />);
    expect(line()).toBe("Create an issue on Jira wasn't approved, so it didn't run.");
  });

  it('asked again only after it had expired: it expired, and says so', () => {
    const expiresAt = Date.now() - 60_000;
    renderInTheme(<ToolApprovalCard approval={approval({ expiresAt })} isLatest={false} closedAt={expiresAt + 1_000} />);
    expect(screen.getByTestId('tool-approval-card').textContent).toBe(
      "Create an issue on Jira didn't run: the request expired. Ask again if it's still needed.",
    );
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

describe('a restricted card: a call that would send what not everyone can open', () => {
  const restricted = (overrides: Partial<ToolApprovalDetails> = {}) =>
    approval({
      serverName: 'Web',
      toolName: 'fetch_url',
      toolTitle: 'Fetch a page',
      restricted: true,
      canRemember: false,
      hosts: [{ host: 'xn--80ak6aa92e.com', unicode: 'аррӏе.com' }],
      arguments: { url: 'https://xn--80ak6aa92e.com/upload', body: `${'a'.repeat(300)}THE END` },
      ...overrides,
    });

  it('says not everyone can open what it may hold, names where it goes in both spellings, and shows the whole call', () => {
    renderInTheme(<ToolApprovalCard approval={restricted()} isLatest />);
    expect(screen.getByTestId('tool-approval-restricted').textContent).toContain(
      'This may include content from sources not everyone can open. Anyone who can read аррӏе.com (xn--80ak6aa92e.com) will see it.',
    );
    expect(screen.getByText('Goes to')).toBeTruthy();
    expect(screen.getByText(`${'a'.repeat(300)}THE END`)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Show all' })).toBeNull();
  });

  it("never offers \"Don't ask again\", and its answer sets no rule", () => {
    renderInTheme(<ToolApprovalCard approval={restricted({ canRemember: true })} isLatest rowId="row-1" />);
    expect(screen.queryByRole('checkbox', { name: "Don't ask again for this tool" })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Allow' }));
    expect(chat.buildStreamChatRequestForSlot).toHaveBeenCalledWith('slot-1', 'Tool approval: allow', undefined, {
      approvalId: 'ap-1',
      decision: 'allow',
    });
  });

  it('names its server when the call names no host, and one spelling when both read the same', () => {
    renderInTheme(<ToolApprovalCard approval={restricted({ serverName: 'Slack', hosts: [] })} isLatest />);
    expect(screen.getByTestId('tool-approval-restricted').textContent).toBe(
      'This may include content from sources not everyone can open. Anyone who can read Slack will see it.',
    );
    cleanup();
    renderInTheme(<ToolApprovalCard approval={restricted({ hosts: [{ host: 'example.com', unicode: 'example.com' }] })} isLatest />);
    expect(screen.getByTestId('tool-approval-restricted').textContent).toContain('Anyone who can read example.com will see it.');
  });

  it('an ordinary card says none of it', () => {
    renderInTheme(<ToolApprovalCard approval={approval()} isLatest />);
    expect(screen.queryByTestId('tool-approval-restricted')).toBeNull();
  });
});

describe('a built-in tool, which has no server', () => {
  // As the server sends the card, and as one saved before it had words for the tool.
  const cards = [
    { instanceId: '', serverName: 'Open a web page', toolName: 'dynamic__fetch_url', toolTitle: 'Open a web page' },
    { instanceId: '', serverName: 'dynamic__fetch_url', toolName: 'dynamic__fetch_url', toolTitle: null },
  ];
  const page = (card: Partial<ToolApprovalDetails>, overrides: Partial<ToolApprovalDetails> = {}) =>
    approval({
      ...card,
      restricted: true,
      canRemember: false,
      hosts: [{ host: 'example.com', unicode: 'example.com' }],
      arguments: { url: 'https://example.com/' },
      ...overrides,
    });

  it.each(cards)('is named in words on the card, with no server and no internal name ($serverName)', (card) => {
    renderInTheme(<ToolApprovalCard approval={page(card)} isLatest />);
    const shown = screen.getByTestId('tool-approval-card');
    expect(screen.getAllByText('Open a web page')).toHaveLength(1);
    expect(shown.querySelector('.rt-Badge')).toBeNull();
    expect(shown.innerHTML).not.toContain('fetch_url');
    expect(shown.innerHTML).not.toContain('Dynamic');
    expect(shown.textContent).toContain('Anyone who can read example.com will see it.');
  });

  it.each(cards)('is named in words in the line others read ($serverName)', (card) => {
    const line = () => screen.getByTestId('tool-approval-card').textContent;
    renderInTheme(<ToolApprovalCard approval={page(card)} isLatest readOnlyFor={{ name: 'Dana' }} />);
    expect(line()).toBe("Open a web page is waiting for Dana's approval.");
    cleanup();
    renderInTheme(<ToolApprovalCard approval={page(card)} isLatest={false} />);
    expect(line()).toBe("Open a web page wasn't approved, so it didn't run.");
    cleanup();
    renderInTheme(<ToolApprovalCard approval={page(card, { expiresAt: 1 })} isLatest />);
    expect(line()).toBe("Open a web page didn't run: the request expired. Ask again if it's still needed.");
    cleanup();
    renderInTheme(<ToolApprovalCard approval={page(card, { expiresAt: 1 })} isLatest readOnlyFor={{ name: 'Dana' }} />);
    expect(line()).toBe('This request expired.');
    cleanup();
    chat.state.slots['slot-1'].isOwner = false;
    renderInTheme(<ToolApprovalCard approval={page(card)} isLatest />);
    expect(line()).toBe("Open a web page is waiting for the conversation owner's approval.");
  });

  it.each([
    ['allowed', 'You allowed Open a web page'],
    ['declined', 'You declined Open a web page'],
    ['expired', "Open a web page didn't run: the request expired"],
  ] as const)('is named in words once it was %s', (approvalAnswer, words) => {
    for (const card of cards) {
      const part = { type: 'tool_call', toolCallId: 'c1', toolName: 'dynamic__fetch_url', status: 'completed', approvalAnswer, approval: page(card) } as MessagePart;
      renderInTheme(<ToolApprovalReceipts items={settledToolApprovals([part])} />);
      expect(screen.getByTestId('tool-approval-receipts').textContent).toBe(words);
      cleanup();
    }
  });

  it('says it may go anywhere when nothing says where', () => {
    renderInTheme(<ToolApprovalCard approval={page({ instanceId: '', serverName: 'Ran code', toolName: 'run_code', toolTitle: null }, { hosts: [] })} isLatest />);
    expect(screen.getByText('Run code')).toBeTruthy();
    expect(screen.getByTestId('tool-approval-restricted').textContent).toBe(
      'This may include content from sources not everyone can open, and it may be sent outside PipesHub.',
    );
  });

  it("a server's tool of the same name keeps its server", () => {
    renderInTheme(<ToolApprovalCard approval={approval({ serverName: 'E2B', toolName: 'run_code', toolTitle: null })} isLatest readOnlyFor={{ name: 'Dana' }} />);
    expect(screen.getByTestId('tool-approval-card').textContent).toBe("Run code on E2B is waiting for Dana's approval.");
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

  it('says why an approved call refused for lost access did not run', () => {
    const refused = [
      {
        type: 'tool_call' as const, toolCallId: 'c1', toolName: 'mcp_jira_create_issue', status: 'blocked' as const,
        approvalAnswer: 'allowed' as const, approvalReason: 'access_lost' as const, approval: approval(),
      },
      { type: 'text' as const, content: "It didn't run.", isFinal: true },
    ];
    renderInTheme(<ChatResponse question="Q" answer="It didn't run." persistedParts={refused} isLastMessage />);

    expect(screen.getByTestId('tool-approval-receipts').textContent).toBe(
      "This didn't run because you no longer have access to something it used.Jira",
    );
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
