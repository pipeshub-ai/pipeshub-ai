import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { McpMyServerEntry } from '@/app/(main)/workspace/mcp-servers/types';

const popup = vi.hoisted(() => ({
  start: vi.fn(),
  options: null as null | { onVerified?: () => void; onFailed?: (f: { reason: string }) => void },
}));
const api = vi.hoisted(() => ({ getCatalogTemplate: vi.fn(async () => ({ typeId: 'exa', authHint: 'Your Exa key' })) }));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/store/toast-store', () => ({ toast }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: api }));
vi.mock('@/app/(main)/workspace/mcp-servers/hooks/use-mcp-oauth-popup', () => ({
  mcpOAuthFailureMessage: () => 'Sign-in was cancelled',
  useMcpOAuthPopup: (options: typeof popup.options) => {
    popup.options = options;
    return { startOAuthPopup: popup.start, status: 'idle', reset: vi.fn() };
  },
}));
vi.mock('@/app/(main)/workspace/mcp-servers/components', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/app/(main)/workspace/mcp-servers/components')>()),
  // The real dialog posts the key; here "Save" stands for a key that was accepted.
  McpAuthDialog: ({ instance, open, onAuthenticated }: { instance: McpMyServerEntry | null; open: boolean; onAuthenticated: () => void }) =>
    open && instance ? (
      <div role="dialog" aria-label={`Key for ${instance.name}`}>
        <button type="button" onClick={onAuthenticated}>
          Save
        </button>
      </div>
    ) : null,
}));

import { McpConnectRows, mcpServersToConnect } from '../mcp-connect-rows';

function entry(overrides: Partial<McpMyServerEntry> = {}): McpMyServerEntry {
  return {
    _id: 'inst-1',
    orgId: 'org-1',
    createdBy: 'u-1',
    name: 'Server',
    transport: 'streamable_http',
    authMode: 'oauth',
    useAdminAuth: false,
    requiredEnv: [],
    optionalEnv: [],
    isCustom: false,
    createdAt: 1,
    updatedAt: 1,
    isAuthenticated: true,
    tools: [],
    ...overrides,
  };
}

const clickUp = entry({ _id: 'cu', name: 'ClickUp', isAuthenticated: false });
const linear = entry({ _id: 'lin', name: 'Linear', toolsErrorCode: 'auth_expired' });
const exaShared = entry({ _id: 'exa', name: 'Exa', authMode: 'api_token', useAdminAuth: true, isAuthenticated: false });
const exaOwnKey = entry({ _id: 'exa', name: 'Exa', typeId: 'exa', authMode: 'api_token', isAuthenticated: false });
const notion = entry({ _id: 'notion', name: 'Notion' });

beforeEach(() => {
  installBrowserShims();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('mcpServersToConnect', () => {
  it('lists the servers a sign-in keeps out, those the person can connect first', () => {
    const servers = mcpServersToConnect([exaShared, clickUp, notion, linear], { isAdmin: false, listed: new Set() });

    expect(servers.map((s) => [s.entry.name, s.state])).toEqual([
      ['Linear', 'needs_reconnect'],
      ['ClickUp', 'needs_connect'],
      ['Exa', 'waiting_for_admin'],
    ]);
  });

  it('leaves out servers already listed with their tools, and disabled ones', () => {
    const disabled = entry({ _id: 'off', name: 'Local', isAuthenticated: false, disabledReason: 'custom_stdio_disabled' });
    const servers = mcpServersToConnect([clickUp, linear, disabled], { isAdmin: false, listed: new Set(['lin']) });

    expect(servers.map((s) => s.entry.name)).toEqual(['ClickUp']);
  });

  it("tells an administrator the shared key is theirs to add", () => {
    const [server] = mcpServersToConnect([exaShared], { isAdmin: true, listed: new Set() });
    expect(server.state).toBe('shared_credential_missing');
  });
});

describe('McpConnectRows', () => {
  function renderRows(entries: McpMyServerEntry[]) {
    const onConnected = vi.fn();
    renderInTheme(
      <McpConnectRows
        servers={mcpServersToConnect(entries, { isAdmin: false, listed: new Set() })}
        rowStyle={{}}
        onConnected={onConnected}
      />,
    );
    const row = (name: string) => screen.getAllByTestId('mcp-connect-row').find((r) => r.textContent?.includes(name)) as HTMLElement;
    return { onConnected, row };
  }

  it('offers Connect, Reconnect, or says it waits for an administrator', () => {
    const { row } = renderRows([clickUp, linear, exaShared]);

    expect(screen.getByText('Not connected')).toBeTruthy();
    expect(within(row('ClickUp')).getByRole('button', { name: 'Connect' })).toBeTruthy();
    expect(within(row('Linear')).getByRole('button', { name: 'Reconnect' })).toBeTruthy();
    expect(within(row('Exa')).queryByRole('button')).toBeNull();
    expect(within(row('Exa')).getByText('Waiting for admin')).toBeTruthy();
  });

  it('signs in with the popup, and the server joins the chat once it is verified', () => {
    const { onConnected, row } = renderRows([clickUp, exaOwnKey]);

    fireEvent.click(within(row('ClickUp')).getByRole('button', { name: 'Connect' }));

    expect(popup.start).toHaveBeenCalledWith('cu');
    // One sign-in at a time.
    expect((within(row('Exa')).getByRole('button', { name: 'Connect' }) as HTMLButtonElement).disabled).toBe(true);

    popup.options?.onVerified?.();
    expect(onConnected).toHaveBeenCalledWith('cu');
    expect(toast.success).toHaveBeenCalled();
  });

  it('says why when the sign-in does not finish', () => {
    const { onConnected, row } = renderRows([clickUp]);
    fireEvent.click(within(row('ClickUp')).getByRole('button', { name: 'Connect' }));

    popup.options?.onFailed?.({ reason: 'cancelled' });

    expect(toast.error).toHaveBeenCalledWith('Sign-in was cancelled');
    expect(onConnected).not.toHaveBeenCalled();
  });

  it('asks for a key in the dialog for a server that takes one', async () => {
    const { onConnected, row } = renderRows([exaOwnKey]);

    fireEvent.click(within(row('Exa')).getByRole('button', { name: 'Connect' }));

    expect(popup.start).not.toHaveBeenCalled();
    expect(api.getCatalogTemplate).toHaveBeenCalledWith('exa');
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Key for Exa' })).getByRole('button', { name: 'Save' }));
    expect(onConnected).toHaveBeenCalledWith('exa');
  });

  it('shows nothing when every server is connected', () => {
    renderRows([notion]);
    expect(screen.queryByText('Not connected')).toBeNull();
  });
});
