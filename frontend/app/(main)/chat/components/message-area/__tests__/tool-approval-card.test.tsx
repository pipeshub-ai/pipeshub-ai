import React from 'react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { ToolApprovalDetails } from '../../../types';

const chat = vi.hoisted(() => ({
  streamMessageForSlot: vi.fn(),
  buildStreamChatRequestForSlot: vi.fn((slotId: string, query: string) => ({ slotId, query })),
  validate: vi.fn(async (..._args: unknown[]): Promise<unknown[]> => []),
  state: { activeSlotId: 'slot-1', slots: { 'slot-1': { isOwner: true as boolean | null, isStreaming: false } } },
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({
  MaterialIcon: ({ name, color }: { name: string; color?: string }) => <span data-icon={name} data-color={color ?? ''} />,
}));
vi.mock('@/app/components/ui/ConnectorIcon', () => ({
  ConnectorIcon: ({ type, color }: { type: string; color?: string }) => <span data-testid="connector-icon" data-type={type} data-color={color ?? ''} />,
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: {} }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: chat.streamMessageForSlot }));
vi.mock('../../../runtime', () => ({ buildStreamChatRequestForSlot: chat.buildStreamChatRequestForSlot }));
vi.mock('../../../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../api')>()),
  validateApprovalEdits: chat.validate,
}));
vi.mock('../../../store', () => ({
  useChatStore: Object.assign(
    (selector: (state: typeof chat.state) => unknown) => selector(chat.state),
    { getState: () => chat.state },
  ),
}));

import { ToolApprovalCard, ToolApprovalReceipts } from '../tool-approval-card';
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

    const title = screen.getByText('Create an issue in Jira');
    expect(title.getAttribute('title')).toBe('create_issue');
    expect(title.className).toMatch(/rt-r-weight-bold/);
    expect(title.className).toMatch(/rt-truncate/);
    expect(screen.queryByText('Jira')).toBeNull();
    expect(screen.getByText(/^Expires in 15 min$/)).toBeTruthy();
    expect(screen.getByText('Login fails')).toBeTruthy();
    expect(screen.getByText('bug')).toBeTruthy();
    expect(screen.queryByText("Can't be undone")).toBeNull();
  });

  it('names a tool without a title readably', () => {
    renderInTheme(<ToolApprovalCard approval={approval({ toolTitle: null, toolName: 'createConfluencePage' })} isLatest />);
    expect(screen.getByText('Create Confluence Page in Jira')).toBeTruthy();
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

    expect(screen.queryByRole('checkbox', { name: "Don't ask again for this tool" })).toBeNull();
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

function editable(overrides: Partial<ToolApprovalDetails> = {}): ToolApprovalDetails {
  return approval({
    editable: true,
    argsHash: 'h1',
    arguments: { title: 'Login fails', labels: ['bug'], meta: { a: 1 } },
    inputSchema: {
      type: 'object',
      required: ['title'],
      properties: { title: { type: 'string' }, labels: { type: 'array', items: { type: 'string' } }, meta: { type: 'object' } },
    },
    ...overrides,
  });
}

const sent = () => chat.buildStreamChatRequestForSlot.mock.calls.at(-1)?.[3];
const titleField = () => screen.getByRole('textbox', { name: /Title/ }) as HTMLInputElement;

describe('ToolApprovalCard: editing and notes', () => {
  it('offers Edit only on a card that can be edited', () => {
    renderInTheme(<ToolApprovalCard approval={approval()} isLatest />);
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    cleanup();

    renderInTheme(<ToolApprovalCard approval={editable({ editable: false })} isLatest />);
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    cleanup();

    renderInTheme(<ToolApprovalCard approval={editable()} isLatest />);
    expect(screen.getByRole('button', { name: 'Edit' })).toBeTruthy();
  });

  it('marks an edited field, hides both "ask again" choices, and Reset puts it back', () => {
    renderInTheme(<ToolApprovalCard approval={editable()} isLatest />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));

    fireEvent.change(titleField(), { target: { value: 'Login broken' } });
    expect(screen.getByText('Edited')).toBeTruthy();
    expect(screen.queryByRole('checkbox', { name: "Don't ask again for this tool" })).toBeNull();
    expect(screen.queryByRole('checkbox', { name: 'Allow for this conversation' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    expect(titleField().value).toBe('Login fails');
    expect(screen.queryByText('Edited')).toBeNull();
    expect(screen.getByRole('checkbox', { name: "Don't ask again for this tool" })).toBeTruthy();
  });

  it('drops a ticked "ask again" choice once the call is edited', () => {
    renderInTheme(<ToolApprovalCard approval={editable()} isLatest rowId="row-1" />);
    fireEvent.click(screen.getByRole('checkbox', { name: "Don't ask again for this tool" }));
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(titleField(), { target: { value: 'Login broken' } });

    fireEvent.click(screen.getByRole('button', { name: 'Deny' }));
    expect(sent()).toEqual({ approvalId: 'ap-1', decision: 'deny', argsHash: 'h1' });

    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    expect(screen.getByRole('checkbox', { name: "Don't ask again for this tool" }).getAttribute('aria-checked')).toBe('false');
  });

  it('treats an emptied optional field that had no value as unchanged', () => {
    renderInTheme(
      <ToolApprovalCard
        approval={editable({
          arguments: { title: 'Login fails' },
          inputSchema: { type: 'object', properties: { title: { type: 'string' }, note: { type: 'string' } } },
        })}
        isLatest
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const noteField = screen.getByRole('textbox', { name: 'Note' });
    fireEvent.change(noteField, { target: { value: 'x' } });
    fireEvent.change(noteField, { target: { value: '' } });
    expect(screen.queryByText('Edited')).toBeNull();
  });

  it('reopens a refused edit on what the person sent, with the errors', () => {
    renderInTheme(
      <ToolApprovalCard
        approval={editable({ editedArguments: { title: 'Bug', labels: ['bug'], meta: { a: 1 } }, editErrors: [{ path: '/title', message: 'Title is too vague' }] })}
        isLatest
      />,
    );
    expect(titleField().value).toBe('Bug');
    expect(screen.getByText('Edited')).toBeTruthy();
    expect(screen.getByText('Title is too vague')).toBeTruthy();
  });

  it('reads "Done" while editing, and that leaves edit mode keeping the edit', () => {
    renderInTheme(<ToolApprovalCard approval={editable()} isLatest />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(titleField(), { target: { value: 'Login broken' } });
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(screen.queryByRole('textbox', { name: /Title/ })).toBeNull();
    expect(screen.getByText('Login broken')).toBeTruthy();
  });

  it('brings its actions into view when it opens in edit mode', async () => {
    const scrollIntoView = vi.fn();
    Object.assign(Element.prototype, { scrollIntoView });
    renderInTheme(<ToolApprovalCard approval={editable({ editErrors: [{ path: '/title', message: 'Title is too vague' }] })} isLatest />);
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' }));
    expect(scrollIntoView.mock.contexts.at(-1)).toBe(screen.getByTestId('tool-approval-actions'));
  });

  it('brings its actions back into view when the server refuses an edit, and focuses the field without scrolling', async () => {
    const scrollIntoView = vi.fn();
    Object.assign(Element.prototype, { scrollIntoView });
    const focus = vi.spyOn(HTMLElement.prototype, 'focus');
    chat.validate.mockResolvedValueOnce([{ path: '/title', message: 'Title is too vague' }]);
    renderInTheme(<ToolApprovalCard approval={editable()} isLatest rowId="row-1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(titleField(), { target: { value: 'Bug' } });
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalled());
    scrollIntoView.mockClear();

    fireEvent.click(screen.getByRole('button', { name: 'Allow with changes' }));

    expect(await screen.findByText('Title is too vague')).toBeTruthy();
    const actions = screen.getByTestId('tool-approval-actions');
    await waitFor(() => expect(scrollIntoView.mock.contexts).toContain(actions));
    expect(focus.mock.contexts.at(-1)).toBe(titleField());
    expect(focus.mock.calls.at(-1)).toEqual([{ preventScroll: true }]);
    focus.mockRestore();
  });

  it('shows nested values read-only', () => {
    renderInTheme(<ToolApprovalCard approval={editable()} isLatest />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    expect(screen.getByText("Can't be edited here")).toBeTruthy();
    expect(screen.queryByRole('textbox', { name: 'Meta' })).toBeNull();
  });

  it('keeps an invalid edit on the card, with the error on the field and focus on it', async () => {
    renderInTheme(<ToolApprovalCard approval={editable()} isLatest rowId="row-1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(titleField(), { target: { value: '' } });

    fireEvent.click(screen.getByRole('button', { name: 'Allow with changes' }));

    expect(await screen.findByText('This field is required.')).toBeTruthy();
    expect(screen.getByText('Fix the highlighted fields')).toBeTruthy();
    expect(document.activeElement).toBe(titleField());
    expect(chat.validate).not.toHaveBeenCalled();
    expect(chat.streamMessageForSlot).not.toHaveBeenCalled();
  });

  it("shows the server's errors on the field and sends nothing", async () => {
    chat.validate.mockResolvedValueOnce([{ path: '/title', message: 'Title is too vague' }]);
    renderInTheme(<ToolApprovalCard approval={editable()} isLatest rowId="row-1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(titleField(), { target: { value: 'Bug' } });

    fireEvent.click(screen.getByRole('button', { name: 'Allow with changes' }));

    expect(await screen.findByText('Title is too vague')).toBeTruthy();
    expect(chat.validate).toHaveBeenCalledWith('ap-1', 'h1', { title: 'Bug', labels: ['bug'], meta: { a: 1 } });
    expect(chat.streamMessageForSlot).not.toHaveBeenCalled();
  });

  it('moves focus into a list field that has the error', async () => {
    chat.validate.mockResolvedValueOnce([{ path: '/labels/0', message: 'Unknown label' }]);
    renderInTheme(<ToolApprovalCard approval={editable()} isLatest rowId="row-1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(titleField(), { target: { value: 'Bug' } });
    fireEvent.click(screen.getByRole('button', { name: 'Allow with changes' }));

    expect(await screen.findByText('Unknown label')).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByRole('group', { name: 'Labels' }).querySelector('input'));
  });

  it('says when the request changed under the card', async () => {
    chat.validate.mockResolvedValueOnce([{ path: '', code: 'requestChanged', message: 'Gone' }]);
    renderInTheme(<ToolApprovalCard approval={editable()} isLatest rowId="row-1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(titleField(), { target: { value: 'Bug' } });
    fireEvent.click(screen.getByRole('button', { name: 'Allow with changes' }));

    expect(await screen.findByText('This request changed. Reload.')).toBeTruthy();
    expect(chat.streamMessageForSlot).not.toHaveBeenCalled();
  });

  it('sends a checked edit with the arguments and the hash it was shown', async () => {
    renderInTheme(<ToolApprovalCard approval={editable()} isLatest rowId="row-1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(titleField(), { target: { value: 'Login broken' } });

    fireEvent.click(screen.getByRole('button', { name: 'Allow with changes' }));

    await waitFor(() => expect(chat.streamMessageForSlot).toHaveBeenCalled());
    expect(sent()).toEqual({
      approvalId: 'ap-1', decision: 'allow', argsHash: 'h1',
      arguments: { title: 'Login broken', labels: ['bug'], meta: { a: 1 } },
    });
  });

  it('leaves out a field the call never set when the person emptied it again', async () => {
    renderInTheme(
      <ToolApprovalCard
        approval={editable({
          arguments: { title: 'Login fails' },
          inputSchema: { type: 'object', properties: { title: { type: 'string' }, assignee: { type: 'string' } } },
        })}
        isLatest
        rowId="row-1"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(titleField(), { target: { value: 'Login broken' } });
    const assignee = screen.getByRole('textbox', { name: /Assignee/ });
    fireEvent.change(assignee, { target: { value: 'sam' } });
    fireEvent.change(assignee, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Allow with changes' }));

    await waitFor(() => expect(chat.streamMessageForSlot).toHaveBeenCalled());
    expect(sent()).toEqual({ approvalId: 'ap-1', decision: 'allow', argsHash: 'h1', arguments: { title: 'Login broken' } });
  });

  it('still sends when the check itself fails, for the server to decide', async () => {
    chat.validate.mockRejectedValueOnce(new Error('offline'));
    renderInTheme(<ToolApprovalCard approval={editable()} isLatest rowId="row-1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(titleField(), { target: { value: 'Login broken' } });
    fireEvent.click(screen.getByRole('button', { name: 'Allow with changes' }));

    await waitFor(() => expect(chat.streamMessageForSlot).toHaveBeenCalled());
    expect(sent()).toMatchObject({ decision: 'allow', arguments: { title: 'Login broken' } });
  });

  it('sends no arguments when nothing was changed', () => {
    renderInTheme(<ToolApprovalCard approval={editable()} isLatest rowId="row-1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.click(screen.getByRole('button', { name: 'Allow' }));

    expect(chat.validate).not.toHaveBeenCalled();
    expect(sent()).toEqual({ approvalId: 'ap-1', decision: 'allow', argsHash: 'h1' });
  });

  it('opens on the errors the server sent back for a refused edit', () => {
    renderInTheme(
      <ToolApprovalCard
        approval={editable({ editErrors: [{ path: '/title', message: 'Title is too vague' }, { path: '', message: 'A rule blocks this action with these details.' }] })}
        isLatest
      />,
    );

    expect(titleField()).toBeTruthy();
    expect(screen.getByText('Title is too vague')).toBeTruthy();
    expect(screen.getByText('A rule blocks this action with these details.')).toBeTruthy();
  });

  it('"Suggest a change" declines with a note for the agent', () => {
    renderInTheme(<ToolApprovalCard approval={editable()} isLatest rowId="row-1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Suggest a change' }));
    const note = screen.getByPlaceholderText('Tell the agent what to change');
    expect(note.getAttribute('maxlength')).toBe('2000');

    fireEvent.change(note, { target: { value: 'Put it on the Q4 board' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send to agent' }));

    expect(sent()).toEqual({ approvalId: 'ap-1', decision: 'deny', argsHash: 'h1', feedback: 'Put it on the Q4 board' });
  });

  it('"Allow for this conversation" and "Don\'t ask again" are one choice or the other', () => {
    renderInTheme(<ToolApprovalCard approval={editable()} isLatest rowId="row-1" />);
    const never = screen.getByRole('checkbox', { name: "Don't ask again for this tool" });
    const chat_ = screen.getByRole('checkbox', { name: 'Allow for this conversation' });
    expect(screen.getByText("It won't ask again in this chat unless the tool changes")).toBeTruthy();

    fireEvent.click(never);
    fireEvent.click(chat_);
    expect(never.getAttribute('aria-checked')).toBe('false');
    fireEvent.click(screen.getByRole('button', { name: 'Allow' }));
    expect(sent()).toEqual({ approvalId: 'ap-1', decision: 'allow', argsHash: 'h1', rememberForConversation: true });

    fireEvent.click(never);
    expect(chat_.getAttribute('aria-checked')).toBe('false');
    fireEvent.click(screen.getByRole('button', { name: 'Allow' }));
    expect(sent()).toEqual({ approvalId: 'ap-1', decision: 'allow', argsHash: 'h1', remember: true });
  });

  it('offers neither "ask again" choice on a card whose rule an admin owns', () => {
    renderInTheme(<ToolApprovalCard approval={approval({ canRemember: false })} isLatest />);
    expect(screen.queryByRole('checkbox', { name: "Don't ask again for this tool" })).toBeNull();
    expect(screen.queryByRole('checkbox', { name: 'Allow for this conversation' })).toBeNull();
  });

  it('Esc leaves edit mode and keeps the edit; Enter in a field sends nothing', () => {
    renderInTheme(<ToolApprovalCard approval={editable()} isLatest rowId="row-1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(titleField(), { target: { value: 'Login broken' } });

    fireEvent.keyDown(titleField(), { key: 'Enter' });
    expect(chat.streamMessageForSlot).not.toHaveBeenCalled();

    fireEvent.keyDown(titleField(), { key: 'Escape' });
    expect(screen.queryByRole('textbox', { name: /Title/ })).toBeNull();
    expect(screen.getByText('Login broken')).toBeTruthy();
  });
});

describe('ToolApprovalCard: house style', () => {
  const variant = (el: HTMLElement) => [...el.classList].find((c) => c.startsWith('rt-variant-'));

  it('card has no amber styling', () => {
    renderInTheme(<ToolApprovalCard approval={editable({ kind: 'destructive' })} isLatest />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(titleField(), { target: { value: 'Login broken' } });
    expect(screen.getByTestId('tool-approval-card').outerHTML).not.toMatch(/amber/);
  });

  it('shows the app icon untinted', () => {
    renderInTheme(<ToolApprovalCard approval={approval()} isLatest />);
    const icon = screen.getByTestId('connector-icon');
    expect(icon.getAttribute('data-type')).toBe('Jira');
    expect(icon.getAttribute('data-color')).toBe('');
  });

  it('Allow is solid and Deny soft', () => {
    renderInTheme(<ToolApprovalCard approval={approval()} isLatest />);
    const allow = screen.getByRole('button', { name: 'Allow' });
    const deny = screen.getByRole('button', { name: 'Deny' });
    expect(variant(allow)).toBe('rt-variant-solid');
    expect(allow.getAttribute('data-accent-color')).not.toBe('red');
    expect(variant(deny)).toBe('rt-variant-soft');
    expect(deny.getAttribute('data-accent-color')).toBe('gray');
    cleanup();

    renderInTheme(<ToolApprovalCard approval={approval({ kind: 'destructive' })} isLatest />);
    expect(screen.getByRole('button', { name: 'Allow' }).getAttribute('data-accent-color')).toBe('red');
  });

  it('Edit and Suggest a change are ghost buttons on the left', () => {
    renderInTheme(<ToolApprovalCard approval={editable()} isLatest />);
    const order = [...screen.getByTestId('tool-approval-actions').querySelectorAll('button')].map((b) => b.textContent);
    expect(order).toEqual(['Edit', 'Suggest a change', 'Deny', 'Allow']);
    expect(variant(screen.getByRole('button', { name: 'Edit' }))).toBe('rt-variant-ghost');
    expect(variant(screen.getByRole('button', { name: 'Suggest a change' }))).toBe('rt-variant-ghost');
  });

  it('edit mode: Edit reads Done, Allow reads Allow with changes only after a change, Reset only after a change', () => {
    renderInTheme(<ToolApprovalCard approval={editable()} isLatest />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    expect(screen.getByRole('button', { name: 'Done' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Allow' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Reset' })).toBeNull();

    fireEvent.change(titleField(), { target: { value: 'Login broken' } });
    expect(screen.getByRole('button', { name: 'Allow with changes' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Reset' })).toBeTruthy();
  });

  it('changed field shows a gray Edited badge', () => {
    renderInTheme(<ToolApprovalCard approval={editable()} isLatest />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(titleField(), { target: { value: 'Login broken' } });
    const badge = screen.getByText('Edited');
    expect(badge.getAttribute('data-accent-color')).toBe('gray');
    expect(variant(badge)).toBe('rt-variant-soft');
  });

  it('narrow: the action groups take full width with no margin pushing past the card', () => {
    renderInTheme(<ToolApprovalCard approval={editable()} isLatest />);
    for (const group of screen.getByTestId('tool-approval-actions').children) {
      expect((group as HTMLElement).style.marginLeft).toBe('');
    }
    const css = readFileSync(resolve(process.cwd(), 'app/globals.css'), 'utf8');
    const narrow = css.match(/@container \(max-width: 460px\) \{([\s\S]*?)\n\}/)?.[1] ?? '';
    expect(narrow).toMatch(/\.tool-approval-card-group \{\s*width: 100%;/);
    expect(narrow).toMatch(/\.tool-approval-card-lead \{\s*margin-left: 0;/);
  });

  it('receipt for allowedNotRun uses orange-11', () => {
    renderInTheme(
      <ToolApprovalReceipts items={[{ key: 'k', tool: 'Create an issue', server: 'Jira', answer: 'allowedNotRun' }] as never} />,
    );
    expect(screen.getByTestId('tool-approval-receipts').querySelector('[data-icon]')?.getAttribute('data-color')).toBe('var(--orange-11)');
  });

  it('reduced motion: no transition style', () => {
    const matchMedia = vi.fn((query: string) => ({ matches: query.includes('reduce'), media: query, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    vi.stubGlobal('matchMedia', matchMedia);
    renderInTheme(<ToolApprovalCard approval={editable()} isLatest />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    // Motion lives only in the stylesheet, where reduced motion turns it off.
    const card = screen.getByTestId('tool-approval-card');
    for (const el of [card, ...card.querySelectorAll<HTMLElement>('[style]')]) {
      expect(el.style.transition).toBe('');
      expect(el.style.animation).toBe('');
    }
    const css = readFileSync(resolve(process.cwd(), 'app/globals.css'), 'utf8');
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\) \{(?:(?!@media)[\s\S])*\.tool-approval-card,\s*\.tool-approval-editor\s*\{\s*animation: none;/);
    vi.unstubAllGlobals();
  });
});

describe('ToolApprovalCard above the composer', () => {
  const composer = (onAnswer = vi.fn(async () => 'sent' as const), onOpenChat = vi.fn()) => {
    renderInTheme(
      <ToolApprovalCard approval={approval()} isLatest placement="composer" conversationTitle="Fix login" onAnswer={onAnswer} onOpenChat={onOpenChat} />,
    );
    return { onAnswer, onOpenChat };
  };

  it('names the chat before the expiry, and Open chat goes there', () => {
    const { onOpenChat } = composer();
    expect(screen.getByText('In "Fix login" · Expires in 15 min')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Open chat' }));
    expect(onOpenChat).toHaveBeenCalled();
  });

  it('has no Open chat inline', () => {
    renderInTheme(<ToolApprovalCard approval={approval()} isLatest />);
    expect(screen.queryByRole('button', { name: 'Open chat' })).toBeNull();
  });

  it('keeps the card and says so when onAnswer throws', async () => {
    composer(vi.fn(async () => {
      throw new Error('offline');
    }));
    fireEvent.click(screen.getByRole('button', { name: 'Deny' }));
    expect(await screen.findByText("Couldn't send your answer. Try again.")).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Deny' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('answers through onAnswer, not the active chat', async () => {
    chat.state.slots['slot-1'].isStreaming = true;
    const { onAnswer } = composer();
    fireEvent.click(screen.getByRole('checkbox', { name: "Don't ask again for this tool" }));
    fireEvent.click(screen.getByRole('button', { name: 'Allow' }));
    await waitFor(() => expect(onAnswer).toHaveBeenCalledWith({ approvalId: 'ap-1', decision: 'allow', remember: true }));
    expect(chat.streamMessageForSlot).not.toHaveBeenCalled();
  });

  it.each([
    ['busy', 'That chat is still working. Try again in a moment.'],
    ['stale', 'That was already answered.'],
    ['failed', "Couldn't send your answer. Try again."],
  ] as const)('says so when the answer comes back %s, and keeps the card', async (result, text) => {
    composer(vi.fn(async () => result));
    fireEvent.click(screen.getByRole('button', { name: 'Deny' }));
    expect(await screen.findByText(text)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Deny' })).toBeTruthy();
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

  it('says what was changed, or the note sent with a decline', () => {
    const settled = [
      {
        type: 'tool_call' as const, toolCallId: 'c1', toolName: 'mcp_jira_create_issue', status: 'completed' as const,
        approvalAnswer: 'allowed' as const, changedFields: ['title', 'labels'], approval: approval(),
      },
      {
        type: 'tool_call' as const, toolCallId: 'c2', toolName: 'mcp_jira_create_issue', status: 'blocked' as const,
        approvalAnswer: 'declined' as const, resultPreview: 'Declined: Put it on the Q4 board', approval: approval(),
      },
      { type: 'text' as const, content: 'Done.', isFinal: true },
    ];
    renderInTheme(<ChatResponse question="Q" answer="Done." persistedParts={settled} isLastMessage />);

    const receipts = screen.getByTestId('tool-approval-receipts');
    expect(receipts.textContent).toContain('Declined: Put it on the Q4 board');
    expect(screen.queryByText('Changed: title, labels')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Allowed with changes' }));
    expect(screen.getByText('Changed: title, labels')).toBeTruthy();
  });

  it('says an edited call that was stopped did not run', () => {
    const stopped = [
      {
        type: 'tool_call' as const, toolCallId: 'c1', toolName: 'mcp_jira_create_issue', status: 'blocked' as const,
        approvalAnswer: 'allowed' as const, changedFields: ['title'], approval: approval(),
      },
      { type: 'text' as const, content: 'It was blocked.', isFinal: true },
    ];
    renderInTheme(<ChatResponse question="Q" answer="It was blocked." persistedParts={stopped} isLastMessage />);
    expect(screen.getByTestId('tool-approval-receipts').textContent).toBe("You allowed Create an issue, but it didn't runJira");
  });

  it('prefers the note the server saved over the shortened preview', () => {
    const declined = [
      {
        type: 'tool_call' as const, toolCallId: 'c1', toolName: 'mcp_jira_create_issue', status: 'blocked' as const,
        approvalAnswer: 'declined' as const, approvalNote: 'Use the full note', resultPreview: 'Declined: Use the', approval: approval(),
      },
      { type: 'text' as const, content: 'OK.', isFinal: true },
    ];
    renderInTheme(<ChatResponse question="Q" answer="OK." persistedParts={declined} isLastMessage />);
    expect(screen.getByTestId('tool-approval-receipts').textContent).toBe('Declined: Use the full noteJira');
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
