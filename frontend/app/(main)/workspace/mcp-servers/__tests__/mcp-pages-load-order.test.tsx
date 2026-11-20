import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useUserStore } from '@/lib/store/user-store';
import type { McpMyServerEntry } from '../types';

const router = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn() }));
const params = vi.hoisted(() => ({ current: new URLSearchParams() }));
const mcpApi = vi.hoisted(() => ({
  getMyMcpServers: vi.fn(),
  getCatalog: vi.fn(),
  listInstances: vi.fn(),
  getInstanceTools: vi.fn(),
  getMyToolRules: vi.fn(),
}));
vi.mock('next/navigation', () => ({ useRouter: () => router, useSearchParams: () => params.current }));
vi.mock('../api', () => ({ McpServersApi: mcpApi }));
vi.mock('@/app/components/ui/service-gate', () => ({
  ServiceGate: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));

import PersonalMcpServersPage from '../personal/page';
import TeamMcpServersPage from '../team/page';

function server(name: string, overrides: Partial<McpMyServerEntry> = {}): McpMyServerEntry {
  return {
    _id: `inst-${name}`, orgId: 'org-1', createdBy: 'u-1', name, transport: 'streamable_http',
    authMode: 'none', useAdminAuth: false, args: [], requiredEnv: [], optionalEnv: [], scopes: [],
    isCustom: true, createdAt: 1, updatedAt: 1, isAuthenticated: true, tools: [], ...overrides,
  };
}

beforeEach(() => {
  installBrowserShims();
  params.current = new URLSearchParams();
  useFeatureFlagsStore.setState({ flags: { ENABLE_MCP: true } } as never);
  mcpApi.getCatalog.mockResolvedValue({ templates: [], customStdioAllowed: false });
  mcpApi.getInstanceTools.mockResolvedValue({ tools: [] });
  mcpApi.getMyToolRules.mockResolvedValue({ tools: {} });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('the personal MCP servers page', () => {
  it('keeps the newest list when an older load answers last', async () => {
    let answerFirst: (value: { instances: McpMyServerEntry[] }) => void = () => {};
    mcpApi.getMyMcpServers
      .mockReturnValueOnce(new Promise((resolve) => { answerFirst = resolve; }))
      .mockResolvedValueOnce({ instances: [server('Newer')] });

    renderInTheme(<PersonalMcpServersPage />);
    await waitFor(() => expect(mcpApi.getMyMcpServers).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(await screen.findByText('Newer')).toBeTruthy();

    await act(async () => answerFirst({ instances: [server('Older')] }));

    expect(screen.getByText('Newer')).toBeTruthy();
    expect(screen.queryByText('Older')).toBeNull();
  });

  it('opens a server on its own page', async () => {
    mcpApi.getMyMcpServers.mockResolvedValue({ instances: [server('Jira')] });
    renderInTheme(<PersonalMcpServersPage />);

    fireEvent.click(await screen.findByRole('link', { name: 'Jira' }));
    expect(router.push).toHaveBeenCalledWith('/workspace/mcp-servers/personal/?serverId=inst-Jira');
  });

  it('shows the server named in the address, and goes back to the list', async () => {
    params.current = new URLSearchParams('serverId=inst-Jira');
    mcpApi.getMyMcpServers.mockResolvedValue({ instances: [server('Jira'), server('Notion')] });
    renderInTheme(<PersonalMcpServersPage />);

    expect(await screen.findByRole('heading', { name: 'Jira' })).toBeTruthy();
    expect(screen.queryByText('Notion')).toBeNull();
    await waitFor(() => expect(mcpApi.getMyToolRules).toHaveBeenCalledWith('inst-Jira'));

    fireEvent.click(screen.getByRole('button', { name: /MCP servers/ }));
    expect(router.push).toHaveBeenCalledWith('/workspace/mcp-servers/personal/');
  });

  it('shows the list when the server in the address is gone', async () => {
    params.current = new URLSearchParams('serverId=inst-Deleted');
    mcpApi.getMyMcpServers.mockResolvedValue({ instances: [server('Jira')] });
    renderInTheme(<PersonalMcpServersPage />);

    expect(await screen.findByRole('link', { name: 'Jira' })).toBeTruthy();
  });
});

describe('the team MCP servers page', () => {
  beforeEach(() => {
    useUserStore.setState({ isInitialized: true, profile: { isAdmin: true } } as never);
    mcpApi.getMyMcpServers.mockResolvedValue({ instances: [] });
  });

  it('lists every organization server on its own row, and links to the personal page', async () => {
    mcpApi.listInstances.mockResolvedValue({
      instances: [server('Jira — Support', { authMode: 'oauth', isAuthenticated: false }), server('Jira — Sales', { authMode: 'oauth', isAuthenticated: false })],
    });
    renderInTheme(<TeamMcpServersPage />);

    expect(await screen.findByRole('link', { name: 'Jira — Support' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Jira — Sales' })).toBeTruthy();
    expect(screen.getByRole('radio', { name: 'Needs action (2)' })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /Your MCP servers/ }));
    expect(router.push).toHaveBeenCalledWith('/workspace/mcp-servers/personal/');
  });

  it('shows the server named in the address with its tools listed, not its rules', async () => {
    params.current = new URLSearchParams('serverId=inst-GitHub');
    mcpApi.listInstances.mockResolvedValue({ instances: [server('GitHub')] });
    renderInTheme(<TeamMcpServersPage />);

    expect(await screen.findByRole('heading', { name: 'GitHub' })).toBeTruthy();
    await waitFor(() => expect(mcpApi.getInstanceTools).toHaveBeenCalledWith('inst-GitHub', { cached: true }));
    expect(mcpApi.getMyToolRules).not.toHaveBeenCalled();
  });
});
