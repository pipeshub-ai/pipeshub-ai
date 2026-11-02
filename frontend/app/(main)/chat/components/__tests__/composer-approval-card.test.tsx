import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { PendingApprovalItem } from '../../types';

const m = vi.hoisted(() => ({
  push: vi.fn(),
  answerElsewhere: vi.fn(async (..._a: unknown[]) => 'sent' as 'sent' | 'busy' | 'stale' | 'failed' | 'refused'),
  fetchPending: vi.fn(async (): Promise<unknown[]> => []),
  toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() },
  state: { activeSlotId: 'slot-1', slots: { 'slot-1': { convId: 'conv-here', isOwner: true, isStreaming: false } } },
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: m.push }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: ({ name }: { name: string }) => <span data-icon={name} /> }));
vi.mock('@/app/components/ui/ConnectorIcon', () => ({ ConnectorIcon: () => <span /> }));
vi.mock('@/lib/store/toast-store', () => ({ toast: m.toast }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: {} }));
vi.mock('../../answer-elsewhere', () => ({ answerElsewhere: m.answerElsewhere }));
vi.mock('../../streaming', () => ({ streamMessageForSlot: vi.fn() }));
vi.mock('../../runtime', () => ({ buildStreamChatRequestForSlot: vi.fn() }));
vi.mock('../../api', () => ({ fetchPendingApprovals: m.fetchPending, validateApprovalEdits: vi.fn(async () => []) }));
vi.mock('../../store', () => ({
  useChatStore: Object.assign((selector: (s: typeof m.state) => unknown) => selector(m.state), { getState: () => m.state }),
}));

import { ComposerApprovalCard } from '../composer-approval-card';
import { resetPendingApprovalsForTest, usePendingApprovals } from '../../pending-approvals-store';

function item(id: string, conversationId: string, title: string, patch: Partial<PendingApprovalItem> = {}): PendingApprovalItem {
  return {
    conversationId,
    conversationTitle: title,
    agentId: null,
    createdAt: Date.now(),
    card: {
      approvalId: id,
      instanceId: 'i',
      serverName: 'Jira',
      toolName: 'create_issue',
      toolTitle: 'Create an issue',
      expiresAt: Date.now() + 12 * 60_000,
      arguments: { title: 'Login fails' },
    },
    ...patch,
  };
}

/** Shows the list and opens the full card (it starts as a one-line bar). */
async function show(items: PendingApprovalItem[]) {
  await showBar(items);
  fireEvent.click(screen.getByRole('button', { name: 'Review' }));
}

async function showBar(items: PendingApprovalItem[]) {
  m.fetchPending.mockResolvedValue(items);
  renderInTheme(<ComposerApprovalCard />);
  await waitFor(() => expect(screen.queryByTestId('composer-approval-bar')).toBeTruthy());
}

/** The card on show; the others stay mounted, hidden, to keep their edits. */
const card = () => screen.getAllByTestId('tool-approval-card').find((el) => !el.closest('[hidden]')) as HTMLElement;
const inCard = (text: RegExp | string) => within(card()).getByText(text);

beforeEach(() => {
  installBrowserShims();
  resetPendingApprovalsForTest();
  vi.clearAllMocks();
  m.answerElsewhere.mockResolvedValue('sent');
});

afterEach(() => cleanup());

describe('ComposerApprovalCard', () => {
  it('shows nothing when no other chat waits', async () => {
    m.fetchPending.mockResolvedValue([item('a', 'conv-here', 'This chat')]);
    renderInTheme(<ComposerApprovalCard />);
    await act(async () => {});
    expect(screen.queryByTestId('composer-approval-bar')).toBeNull();
  });

  it('starts as a one-line bar with no Allow or Deny; Review opens the card, Hide and Esc fold it', async () => {
    await showBar([item('a', 'conv-a', 'Fix login', { createdAt: Date.now() - 3 * 60_000 }), item('b', 'conv-b', 'Weekly report')]);
    // After the list loads: waitFor needs real timers.
    vi.useFakeTimers({ toFake: ['setTimeout'] });
    try {
      const bar = screen.getByTestId('composer-approval-bar');
      expect(bar.textContent).toContain('Create an issue is waiting in "Fix login" · Asked 3 min ago');
      expect(screen.queryByRole('button', { name: 'Allow' })).toBeNull();
      expect(screen.queryByRole('button', { name: 'Deny' })).toBeNull();
      expect(within(bar).getByRole('button', { name: 'Open chat' })).toBeTruthy();
      expect(within(bar).getByRole('button', { name: /\+1 more/ })).toBeTruthy();

      fireEvent.click(screen.getByRole('button', { name: 'Review' }));
      act(() => vi.runAllTimers());
      expect(screen.queryByTestId('composer-approval-bar')).toBeNull();
      expect(screen.getByRole('button', { name: 'Allow' })).toBeTruthy();
      expect(inCard(/In "Fix login" · Expires in 12 min · Asked 3 min ago/)).toBeTruthy();
      expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Hide' }));

      fireEvent.keyDown(screen.getByRole('button', { name: 'Allow' }), { key: 'Escape' });
      act(() => vi.runAllTimers());
      expect(screen.queryByRole('button', { name: 'Allow' })).toBeNull();
      expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Review' }));

      fireEvent.click(screen.getByRole('button', { name: 'Review' }));
      fireEvent.click(screen.getByRole('button', { name: 'Hide' }));
      expect(screen.getByTestId('composer-approval-bar')).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it('folds to the bar while this chat shows its own approval card', async () => {
    await show([item('a', 'conv-a', 'Fix login')]);
    expect(screen.getByRole('button', { name: 'Allow' })).toBeTruthy();
    act(() => usePendingApprovals.getState().markInline(1));
    expect(screen.getByTestId('composer-approval-bar')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Allow' })).toBeNull();
  });

  it('shows In "title", +N more steps through items', async () => {
    await show([item('a', 'conv-a', 'Fix login'), item('b', 'conv-b', 'Weekly report')]);
    expect(inCard(/In "Fix login"/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /\+1 more/ }));
    expect(inCard(/In "Weekly report"/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /\+1 more/ }));
    expect(inCard(/In "Fix login"/)).toBeTruthy();
  });

  it('stepping away and back keeps an edit in progress', async () => {
    const editable = item('a', 'conv-a', 'Fix login');
    editable.card = { ...editable.card, editable: true, argsHash: 'h', inputSchema: { type: 'object', properties: { title: { type: 'string' } } } };
    await show([editable, item('b', 'conv-b', 'Weekly report')]);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Title' }), { target: { value: 'Login fails on Safari' } });
    fireEvent.click(screen.getByRole('button', { name: /\+1 more/ }));
    expect(inCard(/In "Weekly report"/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /\+1 more/ }));
    expect((screen.getByRole('textbox', { name: 'Title' }) as HTMLInputElement).value).toBe('Login fails on Safari');
  });

  it('Allow → toast runningIn with Open chat', async () => {
    await show([item('a', 'conv-a', 'Fix login')]);
    fireEvent.click(screen.getByRole('button', { name: 'Allow' }));
    await waitFor(() => expect(m.toast.success).toHaveBeenCalled());
    expect(m.answerElsewhere.mock.calls[0][1]).toMatchObject({ approvalId: 'a', decision: 'allow' });
    const [title, options] = m.toast.success.mock.calls[0];
    expect(title).toBe('Allowed. Create an issue is running in "Fix login".');
    expect(options.action.label).toBe('Open chat');
    expect(options.placement).toBe('top');
    options.action.onClick();
    expect(m.push).toHaveBeenCalledWith('/chat/?conversationId=conv-a');
    expect(screen.queryByTestId('tool-approval-card')).toBeNull();
  });

  it('Deny → toast declinedIn', async () => {
    await show([item('a', 'conv-a', 'Fix login')]);
    fireEvent.click(screen.getByRole('button', { name: 'Deny' }));
    await waitFor(() => expect(m.toast.success).toHaveBeenCalledWith('Declined in "Fix login".', expect.anything()));
  });

  it('stale item leaves', async () => {
    m.answerElsewhere.mockResolvedValue('stale');
    await show([item('a', 'conv-a', 'Fix login')]);
    fireEvent.click(screen.getByRole('button', { name: 'Allow' }));
    await waitFor(() => expect(m.toast.info).toHaveBeenCalledWith('This was already answered in that chat.', expect.anything()));
    expect(usePendingApprovals.getState().items).toEqual([]);
    expect(screen.queryByTestId('tool-approval-card')).toBeNull();
  });

  it('a busy chat keeps the card and says so', async () => {
    m.answerElsewhere.mockResolvedValue('busy');
    await show([item('a', 'conv-a', 'Fix login')]);
    fireEvent.click(screen.getByRole('button', { name: 'Allow' }));
    expect(await screen.findByText('That chat is still working. Try again in a moment.')).toBeTruthy();
    expect(usePendingApprovals.getState().items).toHaveLength(1);
  });

  it('refused edit keeps the card with field errors', async () => {
    const editable = item('a', 'conv-a', 'Fix login');
    editable.card = {
      ...editable.card,
      editable: true,
      argsHash: 'h',
      inputSchema: { type: 'object', properties: { title: { type: 'string' } } },
    };
    // The stream ended with the card back, carrying the server's errors.
    m.answerElsewhere.mockImplementation(async () => {
      usePendingApprovals.getState().put({ ...editable, card: { ...editable.card, editErrors: [{ path: '/title', message: 'Too long' }] } });
      return 'refused';
    });
    await show([editable]);
    fireEvent.click(screen.getByRole('button', { name: 'Allow' }));
    expect(await screen.findByText('Fix the highlighted fields')).toBeTruthy();
    expect(m.toast.success).not.toHaveBeenCalled();
    expect(usePendingApprovals.getState().items).toHaveLength(1);
  });

  it('a failed answer keeps the card and says why', async () => {
    m.answerElsewhere.mockResolvedValue('failed');
    await show([item('a', 'conv-a', 'Fix login')]);
    fireEvent.click(screen.getByRole('button', { name: 'Allow' }));
    expect(await screen.findByText("Couldn't send your answer. Try again.")).toBeTruthy();
    expect(usePendingApprovals.getState().items).toHaveLength(1);
    expect(m.toast.success).not.toHaveBeenCalled();
  });

  it('after an answer, the newest remaining call shows', async () => {
    await show([item('a', 'conv-a', 'Fix login'), item('b', 'conv-b', 'Weekly report'), item('c', 'conv-c', 'Budget')]);
    fireEvent.click(screen.getByRole('button', { name: /\+2 more/ }));
    fireEvent.click(screen.getByRole('button', { name: /\+2 more/ }));
    expect(inCard(/In "Budget"/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Allow' }));
    await waitFor(() => expect(inCard(/In "Fix login"/)).toBeTruthy());
  });

  it('"+N more" names what it does', async () => {
    await show([item('a', 'conv-a', 'Fix login'), item('b', 'conv-b', 'Weekly report')]);
    expect(screen.getByRole('button', { name: 'Show the next waiting approval (+1 more)' })).toBeTruthy();
  });

  it('Send suggestion → toast says the suggestion went, not Declined', async () => {
    await show([item('a', 'conv-a', 'Fix login')]);
    fireEvent.click(screen.getByRole('button', { name: 'Suggest a change' }));
    fireEvent.change(screen.getByPlaceholderText('Tell PipesHub what to change'), { target: { value: 'Use the other board' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send suggestion' }));
    await waitFor(() => expect(m.toast.success).toHaveBeenCalledWith('Sent your suggestion to "Fix login".', expect.anything()));
    expect(m.answerElsewhere.mock.calls[0][1]).toMatchObject({ decision: 'deny', feedback: 'Use the other board' });
  });

  it('a failure that says why shows the reason', async () => {
    m.answerElsewhere.mockResolvedValue({ failed: 'The tool server is down.' } as never);
    await show([item('a', 'conv-a', 'Fix login')]);
    fireEvent.click(screen.getByRole('button', { name: 'Allow' }));
    expect(await screen.findByText('The tool server is down.')).toBeTruthy();
    expect(usePendingApprovals.getState().items).toHaveLength(1);
  });

  it('a newer call arriving while you edit keeps the same card and your edits', async () => {
    const editable = item('a', 'conv-a', 'Fix login');
    editable.card = { ...editable.card, editable: true, argsHash: 'h', inputSchema: { type: 'object', properties: { title: { type: 'string' } } } };
    await show([editable]);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Title' }), { target: { value: 'Login fails on Safari' } });
    fireEvent.click(screen.getByRole('button', { name: 'Suggest a change' }));

    // A poll brings a newer call first, and the same call back as a fresh object.
    act(() => usePendingApprovals.setState({ items: [item('b', 'conv-b', 'Weekly report'), { ...editable, card: { ...editable.card } }] }));

    expect(inCard(/In "Fix login"/)).toBeTruthy();
    expect((screen.getByRole('textbox', { name: 'Title' }) as HTMLInputElement).value).toBe('Login fails on Safari');
    expect(screen.getByPlaceholderText('Tell PipesHub what to change')).toBeTruthy();
    expect(screen.getByRole('button', { name: /\+1 more/ })).toBeTruthy();
  });

  it('Open chat navigates', async () => {
    await showBar([item('a', 'conv-a', 'Fix login', { agentId: 'agent-7' })]);
    fireEvent.click(screen.getByRole('button', { name: 'Open chat' }));
    expect(m.push).toHaveBeenCalledWith('/chat/?agentId=agent-7&conversationId=conv-a');
  });
});
