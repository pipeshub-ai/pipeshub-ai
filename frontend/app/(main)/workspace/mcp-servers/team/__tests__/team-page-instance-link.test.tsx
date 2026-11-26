/**
 * R27-SEAM: the chat's "approve its tools" link, `?serverId=<id>&tab=hub`, opens that server's page at its admin
 * section; a link written before servers had pages (`?instance=<id>`) is sent there.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import type { McpMyServerEntry } from '../../types';

const router = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn() }));
const params = vi.hoisted(() => ({ value: new URLSearchParams() }));
const mcpApi = vi.hoisted(() => ({ getMyMcpServers: vi.fn(), getCatalog: vi.fn(), listInstances: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => router, useSearchParams: () => params.value }));
vi.mock('../../api', () => ({ McpServersApi: mcpApi }));
vi.mock('@/app/components/ui/service-gate', () => ({
  ServiceGate: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock('@/lib/store/user-store', () => {
  const state = { isAdmin: true, isProfileInitialized: true };
  return {
    useUserStore: (select: (s: typeof state) => unknown) => select(state),
    selectIsAdmin: (s: { isAdmin: boolean }) => s.isAdmin,
    selectIsProfileInitialized: (s: { isProfileInitialized: boolean }) => s.isProfileInitialized,
  };
});
const serverPage = vi.hoisted(() => ({ props: [] as unknown[] }));
vi.mock('../../components', () => ({
  McpAddServerButton: () => null,
  McpAuthDialog: () => null,
  McpServersLayout: () => null,
  McpServerPage: (props: unknown) => {
    serverPage.props.push(props);
    return null;
  },
}));
vi.mock('../components', () => ({ McpInstanceConfigPanel: () => null }));

import TeamMcpServersPage from '../page';

const server = (id: string): McpMyServerEntry => ({
  _id: id, orgId: 'org-1', createdBy: 'u-1', name: id, transport: 'streamable_http', authMode: 'none',
  useAdminAuth: false, args: [], requiredEnv: [], optionalEnv: [], scopes: [], isCustom: true, createdAt: 1,
  updatedAt: 1, isAuthenticated: true, tools: [],
});

const lastPage = () => serverPage.props.at(-1) as { instance: McpMyServerEntry; focusAdminSection?: boolean } | undefined;

beforeEach(() => {
  installBrowserShims();
  serverPage.props = [];
  useFeatureFlagsStore.setState({ flags: { ENABLE_MCP: true }, readFailed: false } as never);
  mcpApi.getCatalog.mockResolvedValue({ templates: [], customStdioAllowed: false });
  mcpApi.getMyMcpServers.mockResolvedValue({ instances: [server('inst-a'), server('inst-b')] });
  mcpApi.listInstances.mockResolvedValue({ instances: [server('inst-a'), server('inst-b')] });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('the team MCP servers page, opened from a link to one server', () => {
  it('opens that server at its admin section once the list is loaded', async () => {
    params.value = new URLSearchParams('serverId=inst-b&tab=hub');
    renderInTheme(<TeamMcpServersPage />);
    await waitFor(() => expect(lastPage()?.instance._id).toBe('inst-b'));
    expect(lastPage()?.focusAdminSection).toBe(true);
  });

  it('opens a server without a tab at its top, and an unknown one not at all', async () => {
    params.value = new URLSearchParams('serverId=inst-a');
    renderInTheme(<TeamMcpServersPage />);
    await waitFor(() => expect(lastPage()?.instance._id).toBe('inst-a'));
    expect(lastPage()?.focusAdminSection).toBe(false);
    cleanup();

    serverPage.props = [];
    params.value = new URLSearchParams('serverId=gone&tab=hub');
    renderInTheme(<TeamMcpServersPage />);
    await waitFor(() => expect(mcpApi.listInstances).toHaveBeenCalledTimes(2));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(lastPage()).toBeUndefined();
  });

  it('sends an older ?instance= link to that server page, keeping its tab', async () => {
    params.value = new URLSearchParams('instance=inst-b&tab=hub');
    renderInTheme(<TeamMcpServersPage />);
    await waitFor(() =>
      expect(router.replace).toHaveBeenCalledWith('/workspace/mcp-servers/team/?serverId=inst-b&tab=hub'),
    );
  });
});
