/**
 * R27-SEAM: the chat's "approve its tools" link, `?instance=<id>&tab=hub`, opens that server's panel on its hub tab.
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
const panel = vi.hoisted(() => ({ states: [] as unknown[] }));
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
vi.mock('../components', () => ({
  McpCatalogLayout: () => null,
  McpServerDetailsLayout: () => null,
  McpInstanceConfigPanel: ({ state }: { state: unknown }) => {
    panel.states.push(state);
    return null;
  },
}));

import TeamMcpServersPage from '../page';

const server = (id: string): McpMyServerEntry => ({
  _id: id, orgId: 'org-1', createdBy: 'u-1', name: id, transport: 'streamable_http', authMode: 'none',
  useAdminAuth: false, args: [], requiredEnv: [], optionalEnv: [], scopes: [], isCustom: true, createdAt: 1,
  updatedAt: 1, isAuthenticated: true, tools: [],
});

const lastState = () => panel.states.at(-1) as { open: boolean; editingInstance: McpMyServerEntry | null; initialTab?: string };

beforeEach(() => {
  installBrowserShims();
  panel.states = [];
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
  it('opens that server on its hub tab once the list is loaded', async () => {
    params.value = new URLSearchParams('instance=inst-b&tab=hub');
    renderInTheme(<TeamMcpServersPage />);
    await waitFor(() => expect(lastState().open).toBe(true));
    expect(lastState().editingInstance?._id).toBe('inst-b');
    expect(lastState().initialTab).toBe('hub');
  });

  it('opens a server without a tab on its configuration, and an unknown one not at all', async () => {
    params.value = new URLSearchParams('instance=inst-a');
    renderInTheme(<TeamMcpServersPage />);
    await waitFor(() => expect(lastState().open).toBe(true));
    expect(lastState().initialTab).toBeUndefined();
    cleanup();

    panel.states = [];
    params.value = new URLSearchParams('instance=gone&tab=hub');
    renderInTheme(<TeamMcpServersPage />);
    await waitFor(() => expect(mcpApi.listInstances).toHaveBeenCalledTimes(2));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(lastState().open).toBe(false);
  });
});
