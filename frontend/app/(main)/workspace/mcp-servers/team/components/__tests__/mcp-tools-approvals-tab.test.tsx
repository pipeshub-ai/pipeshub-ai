import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { McpMyServerEntry } from '../../../types';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { McpToolsApprovalsTab, type McpToolsLoad } from '../mcp-tools-approvals-tab';

const noAuth: McpMyServerEntry = {
  _id: 'inst-1',
  orgId: 'org-1',
  createdBy: 'u-1',
  name: 'Sentry',
  transport: 'streamable_http',
  authMode: 'none',
  useAdminAuth: false,
  requiredEnv: [],
  optionalEnv: [],
  isCustom: true,
  createdAt: 1,
  updatedAt: 1,
  isAuthenticated: true,
  tools: [],
};

const asksForSignIn: McpToolsLoad = {
  status: 'error',
  code: 'reauth',
  message: "This server asks for a sign-in, but it's set to No authentication.",
};

function renderTab(props: Partial<React.ComponentProps<typeof McpToolsApprovalsTab>> = {}) {
  const handlers = { onRefresh: vi.fn(), onAuthenticate: vi.fn(), onReauthenticate: vi.fn(), onEditServer: vi.fn() };
  renderInTheme(
    <McpToolsApprovalsTab instance={noAuth} state="needs_auth_mode" load={asksForSignIn} editor={null} isBusy={false} {...handlers} {...props} />,
  );
  return handlers;
}

beforeEach(() => {
  installBrowserShims();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('McpToolsApprovalsTab, a server set to No authentication that asks for a sign-in', () => {
  it('says so, and Edit server opens its settings instead of asking for a sign-in', () => {
    const handlers = renderTab();

    expect(screen.getByText('This server asks for a sign-in')).toBeTruthy();
    expect(screen.queryByText(/No sign-in needed/)).toBeNull();
    // The generic "Couldn't load the tool list" box would only repeat it.
    expect(screen.queryByText("Couldn't load the tool list")).toBeNull();
    expect(screen.queryByRole('button', { name: /Reconnect|Reauthenticate|Connect/ })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Edit server' }));
    expect(handlers.onEditServer).toHaveBeenCalledOnce();
    expect(handlers.onReauthenticate).not.toHaveBeenCalled();
  });

  it('offers no Edit server to someone who cannot change it', () => {
    renderTab({ onEditServer: undefined });

    expect(screen.getByText('This server asks for a sign-in')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Edit server' })).toBeNull();
  });

  it('a server set to No authentication that answers still says no sign-in is needed', () => {
    renderTab({ state: 'ready', load: { status: 'ready', tools: [] } });

    expect(screen.getByText(/No sign-in needed/)).toBeTruthy();
    expect(screen.queryByText('This server asks for a sign-in')).toBeNull();
  });

  it('still shows a later failure that is not the sign-in request, with Try again', () => {
    const handlers = renderTab({ load: { status: 'error', code: 'unreachable', message: "Couldn't load this MCP server's tools." } });

    expect(screen.getByText("Couldn't load the tool list")).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Try again/ }));
    expect(handlers.onRefresh).toHaveBeenCalledOnce();
  });
});
