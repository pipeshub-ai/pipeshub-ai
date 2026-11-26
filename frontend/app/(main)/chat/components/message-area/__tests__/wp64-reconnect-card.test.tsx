/**
 * WP-64 (G-12 after WP62-TS): "Reconnect GitHub to continue". A step whose service rejected the saved sign-in during
 * the task waits on the chat's connect card, which names the service, says it rejected the sign-in and when, and
 * reconnects it in place; the steps' header reads "Waiting for you to reconnect GitHub".
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
import { connectRequested, rejectedTime } from '../../../connect-cards';
import { connectWaitingLine } from '../connect-card';

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
  displayName: 'GitHub · List issues',
  item: 'GitHub',
  itemKind: 'toolset',
  instanceId: 'inst_jira_1',
  reason: 'sign_in_rejected',
  askedAt: '2026-11-02T00:29:19.000Z',
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

describe('WP-64 reconnect card', () => {
  it('WP64-FE-1: the live frame, the timeline and a saved part carry why it waits and since when', () => {
    expect(connectRequested(FRAME)?.connect).toMatchObject({ item: 'GitHub', reason: 'sign_in_rejected', since: '2026-11-02T00:29:19.000Z' });
    const part = { type: 'approval', kind: 'connect', interactionId: 'ix.connect.t1.0', toolCallId: 'c', status: 'pending',
      connect: { item: 'GitHub', itemKind: 'toolset', connectPath: '/workspace/actions', reason: 'sign_in_rejected', since: '2026-11-02T00:29:19.000Z' } } as unknown as MessagePart;
    expect(approvalFromPart(part)?.connect).toMatchObject({ reason: 'sign_in_rejected', since: '2026-11-02T00:29:19.000Z' });
    const { reason: _reason, askedAt: _at, ...plain } = FRAME;
    void _reason;
    void _at;
    expect(connectRequested(plain)?.connect).not.toHaveProperty('reason');
  });

  it('WP64-FE-2: the owner reads which service rejected the sign-in and when, and reconnects it in place', async () => {
    show(card());
    expect(await screen.findByText('Reconnect GitHub to continue')).toBeTruthy();
    const time = rejectedTime('2026-11-02T00:29:19.000Z');
    expect(screen.getByText(`GitHub rejected the saved sign-in at ${time}. Reconnect it and the task carries on from here.`)).toBeTruthy();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reconnect GitHub' }).hasAttribute('disabled')).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect GitHub' }));
    expect(popup.begin).toHaveBeenCalledTimes(1);
  });

  it('WP64-FE-3: the wait reads as a plain line: you, or the person the task runs for', () => {
    const target = card().connect!;
    expect(connectWaitingLine(i18n.t.bind(i18n), target, true)).toBe('Waiting for you to reconnect GitHub');
    expect(connectWaitingLine(i18n.t.bind(i18n), target, false)).toBe('Waiting for the person this task runs for to reconnect GitHub');
    const { reason: _r, since: _s, ...never } = target;
    void _r;
    void _s;
    expect(connectWaitingLine(i18n.t.bind(i18n), never, true)).toBe('Waiting for you to connect GitHub');
  });

  it('WP64-FE-4: a time today reads as a time; another day with its date', () => {
    const now = new Date('2026-11-02T10:00:00.000Z');
    expect(rejectedTime('2026-11-02T09:29:00.000Z', now)).not.toMatch(/2026/);
    expect(rejectedTime('2026-10-30T09:29:00.000Z', now)).toMatch(/2026/);
    expect(rejectedTime('not a time', now)).toBe('not a time');
  });
});
