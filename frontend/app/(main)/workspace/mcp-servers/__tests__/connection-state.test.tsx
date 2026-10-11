import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { McpMyServerEntry } from '../types';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  apiClient: { post: vi.fn(async () => ({ data: [] })) },
}));

import { mcpConnectionState, usesSharedCredential } from '../connection-state';
import { McpPersonalServerCard } from '../personal/components/mcp-personal-server-card';
import { McpInstanceCard } from '../team/components/mcp-instance-card';
import { McpInstanceRow } from '../team/components/mcp-instance-row';

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

const sharedToken = { authMode: 'api_token', useAdminAuth: true } as const;

beforeEach(() => {
  installBrowserShims();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('usesSharedCredential', () => {
  it('only a token or headers can be shared', () => {
    expect(usesSharedCredential({ authMode: 'api_token', useAdminAuth: true })).toBe(true);
    expect(usesSharedCredential({ authMode: 'headers', useAdminAuth: true })).toBe(true);
    expect(usesSharedCredential({ authMode: 'oauth', useAdminAuth: true })).toBe(false);
    expect(usesSharedCredential({ authMode: 'none', useAdminAuth: true })).toBe(false);
    expect(usesSharedCredential({ authMode: 'api_token', useAdminAuth: false })).toBe(false);
  });
});

describe('mcpConnectionState', () => {
  it('a server without sign-in, or a connected one that answered, is ready', () => {
    expect(mcpConnectionState(entry({ authMode: 'none', isAuthenticated: false }))).toBe('ready');
    expect(mcpConnectionState(entry())).toBe('ready');
    expect(mcpConnectionState(entry({ ...sharedToken }))).toBe('ready');
  });

  it('asks the member to connect when they never have', () => {
    expect(mcpConnectionState(entry({ isAuthenticated: false }))).toBe('needs_connect');
    expect(mcpConnectionState(entry({ authMode: 'api_token', isAuthenticated: false }))).toBe('needs_connect');
  });

  it('a sign-in each person makes for themselves is theirs to make, even when marked shared', () => {
    expect(mcpConnectionState(entry({ useAdminAuth: true, isAuthenticated: false }))).toBe('needs_connect');
  });

  it('a missing shared credential is the administrator’s to add', () => {
    const missing = entry({ ...sharedToken, isAuthenticated: false });
    expect(mcpConnectionState(missing)).toBe('waiting_for_admin');
    expect(mcpConnectionState(missing, { isAdmin: true })).toBe('shared_credential_missing');
  });

  it.each(['auth_expired', 'unauthorized', 'needs_permission'] as const)('a %s sign-in needs reconnecting', (code) => {
    expect(mcpConnectionState(entry({ toolsError: 'x', toolsErrorCode: code }))).toBe('needs_reconnect');
  });

  it.each(['auth_expired', 'unauthorized'] as const)('a shared credential that is %s goes back to the administrator', (code) => {
    const rejected = entry({ ...sharedToken, toolsError: 'x', toolsErrorCode: code });
    expect(mcpConnectionState(rejected)).toBe('waiting_for_admin');
    expect(mcpConnectionState(rejected, { isAdmin: true })).toBe('shared_credential_missing');
  });

  it('a server that missed the time budget is slow, not broken', () => {
    expect(mcpConnectionState(entry({ toolsError: 'x', toolsErrorCode: 'timeout', toolsTimedOut: true }))).toBe('slow');
  });

  it('a server the deployment refuses is blocked, not just unreachable', () => {
    expect(mcpConnectionState(entry({ toolsError: 'x', toolsErrorCode: 'blocked' }))).toBe('blocked');
  });

  it.each(['unreachable', 'error'] as const)('a %s server is unreachable', (code) => {
    expect(mcpConnectionState(entry({ toolsError: 'x', toolsErrorCode: code }))).toBe('unreachable');
  });

  it('reads responses that carry no code', () => {
    expect(mcpConnectionState(entry({ toolsError: 'x', toolsTimedOut: true }))).toBe('slow');
    expect(mcpConnectionState(entry({ toolsError: 'x' }))).toBe('unreachable');
  });
});

function renderPersonalCard(instance: McpMyServerEntry) {
  const handlers = { onAuthenticate: vi.fn(), onReauthenticate: vi.fn(), onRemoveCredentials: vi.fn() };
  renderInTheme(<McpPersonalServerCard instance={instance} isBusy={false} {...handlers} />);
  return handlers;
}

describe('McpPersonalServerCard status', () => {
  it('an expired sign-in says so and offers Reconnect', () => {
    const handlers = renderPersonalCard(
      entry({ toolsError: 'Sign-in expired. Reconnect this server.', toolsErrorCode: 'auth_expired' }),
    );

    expect(screen.getByText('Reconnect needed')).toBeTruthy();
    expect(screen.queryByText('Ready')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }));
    expect(handlers.onReauthenticate).toHaveBeenCalledOnce();
    expect(handlers.onAuthenticate).not.toHaveBeenCalled();
  });

  it('a member waiting on a shared credential sees no button they cannot use', () => {
    renderPersonalCard(entry({ ...sharedToken, isAuthenticated: false }));

    expect(screen.getByText('Waiting for admin')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Connect' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Reconnect' })).toBeNull();
  });

  it('a server never connected offers Connect', () => {
    const handlers = renderPersonalCard(entry({ isAuthenticated: false }));

    expect(screen.getByText('Not connected')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    expect(handlers.onAuthenticate).toHaveBeenCalledOnce();
  });

  it('a slow server shows its note without a sign-in button', () => {
    renderPersonalCard(entry({ toolsError: 'Took too long to list tools', toolsErrorCode: 'timeout', toolsTimedOut: true }));

    expect(screen.getByText('Slow to respond')).toBeTruthy();
    expect(screen.getByTitle('Took too long to list tools')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Reconnect' })).toBeNull();
  });

  it('tools from the cache say when they were listed', () => {
    const tool = { name: 'search', namespacedName: 'mcp_jira_search', inputSchema: {} };
    const listedAt = Date.UTC(2026, 9, 7, 9, 30);
    renderPersonalCard(entry({ isAuthenticated: true, tools: [tool], toolsCachedAt: listedAt }));

    expect(screen.getByText('1 tool').getAttribute('title')).toBe(`Tools as of ${new Date(listedAt).toLocaleString()}`);
  });

  it('tools listed just now carry no date', () => {
    const tool = { name: 'search', namespacedName: 'mcp_jira_search', inputSchema: {} };
    renderPersonalCard(entry({ isAuthenticated: true, tools: [tool] }));

    expect(screen.getByText('1 tool').getAttribute('title')).toBeNull();
  });

  it('an OAuth server marked shared still lets each person connect', () => {
    const handlers = renderPersonalCard(entry({ useAdminAuth: true, isAuthenticated: false }));

    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    expect(handlers.onAuthenticate).toHaveBeenCalledOnce();
  });

  it('a server that fails to answer is not shown as ready', () => {
    renderPersonalCard(entry({ authMode: 'none', toolsError: 'Connection refused', toolsErrorCode: 'unreachable' }));

    expect(screen.getByText("Can't reach server")).toBeTruthy();
    expect(screen.queryByText('Ready')).toBeNull();
  });
});

describe('team page status', () => {
  it('the card tells an administrator the shared credential is missing', () => {
    renderInTheme(
      <McpInstanceCard instance={entry({ ...sharedToken, isAuthenticated: false })} onEdit={() => {}} onDelete={() => {}} />,
    );

    expect(screen.getByText('Shared credential missing')).toBeTruthy();
    expect(screen.queryByText('Ready')).toBeNull();
  });

  it('the row offers Reconnect for an expired sign-in', () => {
    const onReauthenticate = vi.fn();
    renderInTheme(
      <McpInstanceRow
        instance={entry({ toolsError: 'Sign-in expired', toolsErrorCode: 'auth_expired' })}
        isBusy={false}
        onManage={() => {}}
        onDelete={() => {}}
        onAuthenticate={() => {}}
        onReauthenticate={onReauthenticate}
        onDisconnect={() => {}}
        onDiscoverTools={() => {}}
      />,
    );

    expect(screen.getAllByText('Reconnect needed').length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }));
    expect(onReauthenticate).toHaveBeenCalledOnce();
  });
});
