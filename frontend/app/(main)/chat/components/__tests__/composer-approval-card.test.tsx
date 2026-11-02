import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { PendingApprovalItem } from '../../types';

const m = vi.hoisted(() => ({
  push: vi.fn(),
  answerElsewhere: vi.fn(async (..._a: unknown[]) => 'sent' as 'sent' | 'busy' | 'stale' | 'failed'),
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

async function show(items: PendingApprovalItem[]) {
  m.fetchPending.mockResolvedValue(items);
  renderInTheme(<ComposerApprovalCard />);
  await waitFor(() => expect(screen.queryAllByTestId('tool-approval-card').length).toBeGreaterThan(0));
}

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
    expect(screen.queryByTestId('tool-approval-card')).toBeNull();
  });

  it('shows In "title", +N more steps through items', async () => {
    await show([item('a', 'conv-a', 'Fix login'), item('b', 'conv-b', 'Weekly report')]);
    expect(screen.getByText(/In "Fix login"/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '+1 more' }));
    expect(screen.getByText(/In "Weekly report"/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '+1 more' }));
    expect(screen.getByText(/In "Fix login"/)).toBeTruthy();
  });

  it('Allow → toast runningIn with Open chat', async () => {
    await show([item('a', 'conv-a', 'Fix login')]);
    fireEvent.click(screen.getByRole('button', { name: 'Allow' }));
    await waitFor(() => expect(m.toast.success).toHaveBeenCalled());
    expect(m.answerElsewhere.mock.calls[0][1]).toMatchObject({ approvalId: 'a', decision: 'allow' });
    const [title, options] = m.toast.success.mock.calls[0];
    expect(title).toBe('Allowed. Create an issue is running in Fix login.');
    expect(options.action.label).toBe('Open chat');
    options.action.onClick();
    expect(m.push).toHaveBeenCalledWith('/chat/?conversationId=conv-a');
    expect(screen.queryByTestId('tool-approval-card')).toBeNull();
  });

  it('Deny → toast declinedIn', async () => {
    await show([item('a', 'conv-a', 'Fix login')]);
    fireEvent.click(screen.getByRole('button', { name: 'Deny' }));
    await waitFor(() => expect(m.toast.success).toHaveBeenCalledWith('Declined in Fix login.', expect.anything()));
  });

  it('stale item leaves', async () => {
    m.answerElsewhere.mockResolvedValue('stale');
    await show([item('a', 'conv-a', 'Fix login')]);
    fireEvent.click(screen.getByRole('button', { name: 'Allow' }));
    await waitFor(() => expect(m.toast.info).toHaveBeenCalledWith('That was already answered.'));
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
    await show([editable]);
    fireEvent.click(screen.getByRole('button', { name: 'Allow' }));
    await waitFor(() => expect(screen.queryByTestId('tool-approval-card')).toBeNull());
    // The stream ended with the card back, carrying the server's errors.
    act(() => usePendingApprovals.getState().put({ ...editable, card: { ...editable.card, editErrors: [{ path: '/title', message: 'Too long' }] } }));
    expect(await screen.findByText('Fix the highlighted fields')).toBeTruthy();
  });

  it('Open chat navigates', async () => {
    await show([item('a', 'conv-a', 'Fix login', { agentId: 'agent-7' })]);
    fireEvent.click(screen.getByRole('button', { name: 'Open chat' }));
    expect(m.push).toHaveBeenCalledWith('/chat/?agentId=agent-7&conversationId=conv-a');
  });
});
