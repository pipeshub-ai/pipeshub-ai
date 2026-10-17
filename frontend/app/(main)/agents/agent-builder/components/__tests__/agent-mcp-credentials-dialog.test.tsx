import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '../../__tests__/agent-builder-harness';
import type { McpMyServerEntry } from '../../../../workspace/mcp-servers/types';

const mcpApi = vi.hoisted(() => ({
  getOAuthAuthorizationUrl: vi.fn(),
  reauthenticate: vi.fn(),
  reauthenticateAgentInstance: vi.fn(),
  getMyMcpServers: vi.fn(),
}));
vi.mock('../../../../workspace/mcp-servers/api', () => ({ McpServersApi: mcpApi }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { McpCredentialsDialog } from '../agent-mcp-credentials-dialog';

function connectedOAuthServer(): McpMyServerEntry {
  return {
    _id: 'inst-1', orgId: 'org-1', createdBy: 'u-1', name: 'GitHub', transport: 'streamable_http',
    authMode: 'oauth', useAdminAuth: false, requiredEnv: [], optionalEnv: [], isCustom: false,
    createdAt: 1, updatedAt: 1, isAuthenticated: true, tools: [],
  };
}

beforeEach(() => {
  installBrowserShims();
  mcpApi.getOAuthAuthorizationUrl.mockResolvedValue({ authorizationUrl: 'https://auth.example.com/authorize' });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe('McpCredentialsDialog reconnect', () => {
  it('starts a new sign-in without deleting the working token first', async () => {
    const popup = { closed: false, location: { href: 'about:blank' }, focus: vi.fn(), close: vi.fn() };
    const open = vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
    renderInTheme(
      <McpCredentialsDialog instance={connectedOAuthServer()} onClose={() => {}} onSuccess={() => {}} />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }));

    await waitFor(() => expect(popup.location.href).toBe('https://auth.example.com/authorize'));
    expect(open.mock.calls[0][0]).toBe('about:blank');
    expect(mcpApi.reauthenticate).not.toHaveBeenCalled();
    expect(mcpApi.reauthenticateAgentInstance).not.toHaveBeenCalled();
  });

  it('says why when the popup is blocked', async () => {
    vi.spyOn(window, 'open').mockReturnValue(null);
    renderInTheme(
      <McpCredentialsDialog instance={connectedOAuthServer()} onClose={() => {}} onSuccess={() => {}} />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }));

    expect(await screen.findByText(/Popup blocked/)).toBeTruthy();
  });
});
