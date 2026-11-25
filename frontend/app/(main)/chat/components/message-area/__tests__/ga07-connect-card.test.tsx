/**
 * GA-07 (G-12): "Connect Jira to continue". A call waiting for its owner to connect an item shows a card where an
 * approval would sit; Connect opens the chat's own sign-in and, when it succeeds, the card answers the wait itself;
 * Skip goes on without it; someone else may only skip; the saved reply keeps a receipt that names the item.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import '@/lib/__tests__/test-i18n';
import type { MessagePart, PendingApproval } from '../../../types';

type PopupOptions = { verifyAuthenticated: () => Promise<boolean>; onVerified: () => void; onIncomplete: () => void };

const popup = vi.hoisted(() => ({ begin: vi.fn(), stop: vi.fn(), options: null as null | PopupOptions }));
const mcpPopup = vi.hoisted(() => ({ start: vi.fn(), options: null as null | { onVerified: () => void } }));
const toolsets = vi.hoisted(() => ({
  findMyToolsetByInstanceId: vi.fn(async () => ({ instanceId: 'inst_jira_1', authType: 'OAUTH', isAuthenticated: false })),
  getInstanceOAuthAuthorizationUrl: vi.fn(async () => ({ success: true, authorizationUrl: 'https://auth.example/start' })),
}));
const mcp = vi.hoisted(() => ({
  getMyMcpServers: vi.fn(async () => ({ instances: [{ _id: 'mcp-9', name: 'GitHub', authMode: 'oauth', isAuthenticated: false }] })),
}));

vi.mock('@/app/(main)/toolsets/api', () => ({ ToolsetsApi: toolsets }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: mcp }));
vi.mock('@/app/(main)/agents/agent-builder/hooks/use-toolset-oauth-popup-flow', () => ({
  useToolsetOauthPopupFlow: (options: PopupOptions) => {
    popup.options = options;
    return { authenticating: false, beginOAuth: popup.begin, stopOAuthUi: popup.stop };
  },
}));
vi.mock('@/app/(main)/workspace/mcp-servers/hooks/use-mcp-oauth-popup', () => ({
  useMcpOAuthPopup: (options: { onVerified: () => void }) => {
    mcpPopup.options = options;
    return { startOAuthPopup: mcpPopup.start };
  },
  mcpOAuthFailureMessage: () => 'failed',
}));

import { ApprovalInteraction } from '../interaction-card';
import { approvalFromPart } from '../../../durable-run-ui';
import { groupApprovals } from '../../../interactions';
import { connectRequested, connectResolved, pendingConnects } from '../../../connect-cards';
import { withApprovalRequested, withApprovalResolved } from '../../../approvals';

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
beforeEach(() => {
  popup.begin.mockReset();
  mcpPopup.start.mockReset();
});
afterEach(() => cleanup());

const FRAME = {
  interactionId: 'ix.connect.t1.0',
  toolCallId: 'call-search',
  toolName: 'jira__search_issues',
  displayName: 'Jira · Search issues',
  item: 'Jira',
  itemKind: 'toolset',
  instanceId: 'inst_jira_1',
  connectPath: '/workspace/actions',
  status: 'pending',
  seq: 13,
  afterTurns: 1,
};

const card = (over: Partial<PendingApproval> = {}): PendingApproval => ({
  ...(connectRequested(FRAME) as PendingApproval),
  status: 'pending',
  errorCode: null,
  runId: 'run-1',
  ...over,
});

function show(item: PendingApproval, isOwner = true, state: 'active' | 'done' = 'active') {
  const onDecide = vi.fn();
  render(
    <Theme>
      <ApprovalInteraction
        group={{ key: 'k', items: [item] }}
        state={state}
        onDecide={onDecide}
        onDecideAll={vi.fn()}
        conversationChoice
        paused={false}
        isOwner={isOwner}
      />
    </Theme>,
  );
  return onDecide;
}

describe('GA-07 connect card (G-12)', () => {
  it('GA-07: the frame becomes a card of kind connect that names the item, and its end a decision', () => {
    const asked = connectRequested(FRAME);
    expect(asked).toMatchObject({
      interactionId: 'ix.connect.t1.0',
      kind: 'connect',
      connect: { item: 'Jira', itemKind: 'toolset', instanceId: 'inst_jira_1', connectPath: '/workspace/actions' },
      seq: 13,
      afterTurns: 1,
    });
    expect(connectRequested({ ...FRAME, connectPath: 'https://evil.example/' }), 'only a path inside the app').toBeNull();
    expect(connectRequested({ ...FRAME, interactionId: 'ix.approve.t1.0' })).toBeNull();
    const statuses = ['connected', 'declined', 'expired', 'closed'].map((status) => {
      const list = withApprovalRequested([], asked as never);
      return withApprovalResolved(list, connectResolved({ interactionId: FRAME.interactionId, status }) as never)[0]?.status;
    });
    expect(statuses).toEqual(['approved', 'denied', 'expired', 'closed']);
    expect(pendingConnects([FRAME as never, { ...FRAME, interactionId: 'ix.connect.t2.0', status: 'connected' } as never])).toHaveLength(1);
  });

  it('GA-07: a connect card is never batched with another call', () => {
    const one = card();
    const two = card({ interactionId: 'ix.connect.t1.1' });
    expect(groupApprovals([one, two])).toHaveLength(2);
  });

  it("GA-07: the owner reads what the step needs, in words, and can connect or skip; no ids", async () => {
    const onDecide = show(card());
    const region = screen.getByRole('region', { name: 'Connect Jira to continue' });
    expect(region.textContent).toContain('This step needs your Jira account to search issues.');
    expect(region.textContent).not.toContain('inst_jira_1');
    expect(region.textContent).not.toContain('ix.connect');
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }));
    expect(onDecide).toHaveBeenCalledWith(expect.objectContaining({ interactionId: 'ix.connect.t1.0' }), 'denied', 'once');
  });

  it('GA-07: Connect opens the OAuth sign-in and a sign-in that succeeds answers the wait itself', async () => {
    const onDecide = show(card());
    const connect = screen.getByRole('button', { name: 'Connect Jira' });
    await waitFor(() => expect((connect as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(connect);
    expect(popup.begin).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('region').textContent).toContain('Finish signing in to Jira in the window that opened.');
    act(() => popup.options?.onVerified());
    expect(onDecide).toHaveBeenCalledWith(expect.objectContaining({ interactionId: 'ix.connect.t1.0' }), 'approved', 'once');
  });

  it("GA-07: a sign-in that does not finish says so and offers to try again or carry on after connecting elsewhere", async () => {
    const onDecide = show(card());
    const connect = screen.getByRole('button', { name: 'Connect Jira' });
    await waitFor(() => expect((connect as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(connect);
    act(() => popup.options?.onIncomplete());
    expect(screen.getByRole('alert').textContent).toBe('Jira did not finish signing you in. Try again.');
    expect(screen.getByRole('region').textContent).toContain('Connected in another tab? Tell the task to carry on.');
    fireEvent.click(screen.getByRole('button', { name: "I've connected it" }));
    await waitFor(() => expect(screen.getAllByRole('alert').map((a) => a.textContent)).toContain("Jira isn't connected yet. Connect it, then try again."));
    expect(onDecide).not.toHaveBeenCalled();
    toolsets.findMyToolsetByInstanceId.mockResolvedValueOnce({ instanceId: 'inst_jira_1', authType: 'OAUTH', isAuthenticated: true });
    fireEvent.click(screen.getByRole('button', { name: "I've connected it" }));
    await waitFor(() => expect(onDecide).toHaveBeenCalledWith(expect.anything(), 'approved', 'once'));
  });

  it("GA-07: an MCP server's card opens its OAuth sign-in", async () => {
    show(card({ connect: { item: 'GitHub', itemKind: 'mcp', instanceId: 'mcp-9', connectPath: '/workspace/mcp-servers/personal' } }));
    expect(screen.getByRole('region').textContent).toContain('This step needs your GitHub MCP server.');
    const connect = screen.getByRole('button', { name: 'Connect GitHub' });
    await waitFor(() => expect((connect as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(connect);
    expect(mcpPopup.start).toHaveBeenCalledWith('mcp-9');
  });

  it('GA-07: someone else viewing sees who it waits on and may only skip it', () => {
    show(card(), false);
    const region = screen.getByRole('region', { name: 'Waiting for the person this task runs for to connect Jira' });
    expect(region.textContent).toContain('Only they can connect their Jira account. You can skip this step for them.');
    expect(screen.queryByRole('button', { name: 'Connect Jira' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Skip' })).toBeTruthy();
  });

  it('GA-07: the saved reply keeps one receipt line per wait, naming the item, without its instance', () => {
    const saved = (status: string): MessagePart => ({
      type: 'approval',
      kind: 'connect',
      interactionId: 'ix.connect.t1.0',
      toolCallId: 'call-search',
      toolName: 'jira__search_issues',
      connect: { item: 'Jira', itemKind: 'toolset', connectPath: '/workspace/actions' },
      status: status as never,
      seq: 13,
      afterTurns: 1,
      runId: 'run-1',
    });
    const receipt = (status: string) => {
      cleanup();
      const item = approvalFromPart(saved(status));
      expect(item?.kind).toBe('connect');
      expect(item?.connect?.instanceId).toBeUndefined();
      show(item as PendingApproval, true, 'done');
      // The receipt's icon is a ligature (its name is text): the words are the line's own.
      return screen.getByTestId('interaction-receipt').querySelector('span.rt-Text')?.textContent;
    };
    expect(receipt('approved')).toBe('Jira connected · the task went on');
    expect(receipt('denied')).toBe('Skipped · the task went on without Jira');
    expect(receipt('expired')).toBe('Not connected in time · the task went on without Jira');
    expect(receipt('closed')).toBe('The task ended before Jira was connected');
  });
});
