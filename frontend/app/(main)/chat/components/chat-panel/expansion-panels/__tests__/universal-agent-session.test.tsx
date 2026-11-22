/**
 * The chat's Actions and MCP tabs keep the person's lists between visits. On the
 * desktop app signing out does not reload the page, so these lists must not
 * reach the next person, and an answer that lands after the sign-out must not
 * fill them again. The late answers go through the real apiClient, whose
 * session guard drops them.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen } from '@testing-library/react';
import { AxiosHeaders, AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import type { McpMyServerEntry } from '@/app/(main)/workspace/mcp-servers/types';

const api = vi.hoisted(() => ({
  getMyToolsets: vi.fn(),
  getAllMyToolsets: vi.fn(),
  findMyToolsetByInstanceId: vi.fn(),
  getInstanceOAuthAuthorizationUrl: vi.fn(),
}));
const apiActionsApi = vi.hoisted(() => ({ listInstances: vi.fn() }));
const mcpApi = vi.hoisted(() => ({ getMyMcpServers: vi.fn() }));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/store/toast-store', () => ({ toast }));
vi.mock('@/app/(main)/toolsets/api', () => ({ ToolsetsApi: api, MAX_TOOLSETS_LIST_LIMIT: 200 }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: mcpApi }));
vi.mock('@/app/(main)/workspace/api-toolsets/api', () => ({ ApiToolsetsApi: apiActionsApi }));
vi.mock('@/app/(main)/workspace/api-toolsets/components', () => ({ ApiActionConnectionPanel: () => null }));
vi.mock('../connectors-collections/collections-tab', () => ({ CollectionsTab: () => null }));
vi.mock('@/app/(main)/agents/agent-builder/components/user-toolset-config-dialog', () => ({
  UserToolsetConfigDialog: () => null,
}));

import { apiClient } from '@/lib/api/axios-instance';
import { useChatStore } from '@/chat/store';
import { useAuthStore } from '@/lib/store/auth-store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { UniversalAgentResourcesPanel } from '../universal-agent-resources-panel';

function toolset(name: string): BuilderSidebarToolset {
  return {
    name,
    normalized_name: name,
    displayName: name,
    description: '',
    iconPath: '',
    category: 'app',
    toolCount: 1,
    tools: [{ name: 'run', fullName: `${name}.run`, description: '' }],
    isConfigured: true,
    isAuthenticated: true,
    isFromRegistry: false,
    toolsetType: name,
    authType: 'OAUTH',
    instanceId: `${name}-1`,
    instanceName: name,
  };
}

function mcpServer(name: string): McpMyServerEntry {
  return {
    _id: `${name}-1`,
    name,
    typeId: name,
    isAuthenticated: true,
    tools: [{ name: 'list', namespacedName: `mcp_${name}_list`, inputSchema: {}, kind: 'read' }],
  } as unknown as McpMyServerEntry;
}

/** apiClient requests answered by the test, so the session can end before the answer. */
const pending: Array<{ answer: (status: number, data?: unknown) => void }> = [];
function deferredAdapter(config: InternalAxiosRequestConfig) {
  return new Promise<AxiosResponse>((resolve, reject) => {
    pending.push({
      answer: (status, data = {}) => {
        const response = { data, status, statusText: '', headers: new AxiosHeaders(), config };
        if (status < 400) resolve(response);
        else reject(new AxiosError('failed', 'ERR_BAD_RESPONSE', config, null, response));
      },
    });
  });
}
const viaApiClient = <T,>() => apiClient.get<T>('/api/v1/test').then((r) => r.data);

const toolLabels = () => useChatStore.getState().universalAgentToolGroups.map((g) => g.label);
const mcpLabels = () => useChatStore.getState().universalAgentMcpGroups.map((g) => g.label);

function serve(toolsets: string[], servers: string[] = []) {
  api.getMyToolsets.mockResolvedValue({ toolsets: toolsets.map(toolset), hasNext: false });
  api.getAllMyToolsets.mockResolvedValue({ toolsets: [] });
  mcpApi.getMyMcpServers.mockResolvedValue({ instances: servers.map(mcpServer) });
}

async function flush() {
  await act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
  });
}

function signOutAndIn() {
  act(() => useAuthStore.getState().logout());
  useAuthStore.setState({ accessToken: 'token-b', refreshToken: 'refresh-b', isAuthenticated: true });
}

beforeEach(() => {
  installBrowserShims();
  pending.length = 0;
  apiClient.defaults.adapter = deferredAdapter;
  useAuthStore.setState({ accessToken: 'token-a', refreshToken: 'refresh-a', isAuthenticated: true });
  useFeatureFlagsStore.setState({ flags: { ENABLE_ACTIONS: true, ENABLE_MCP: true } });
  // A fresh session per test: the panel's lists and the chat store start empty.
  act(() => useAuthStore.getState().logout());
  useAuthStore.setState({ accessToken: 'token-a', refreshToken: 'refresh-a', isAuthenticated: true });
  useFeatureFlagsStore.setState({ flags: { ENABLE_ACTIONS: true, ENABLE_MCP: true } });
  apiActionsApi.listInstances.mockResolvedValue([]);
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("the chat's Actions and MCP lists at session end", () => {
  it('user A signs out and B signs in without a reload: B sees only B’s actions and MCP servers', async () => {
    serve(['a-slack'], ['a-github']);
    const a = renderInTheme(<UniversalAgentResourcesPanel />);
    await flush();
    expect(toolLabels()).toEqual(['a-slack']);
    expect(mcpLabels()).toEqual(['a-github']);
    a.unmount();

    signOutAndIn();
    useFeatureFlagsStore.setState({ flags: { ENABLE_ACTIONS: true, ENABLE_MCP: true } });
    serve(['b-jira'], ['b-linear']);
    renderInTheme(<UniversalAgentResourcesPanel />);
    fireEvent.click(screen.getByRole('tab', { name: 'Actions' }));
    await flush();

    // Within five minutes of A's load, yet asked again for B.
    expect(api.getMyToolsets).toHaveBeenCalledTimes(2);
    expect(mcpApi.getMyMcpServers).toHaveBeenCalledTimes(2);
    expect(toolLabels()).toEqual(['b-jira']);
    expect(mcpLabels()).toEqual(['b-linear']);
    expect(screen.queryByText('a-slack')).toBeNull();
  });

  it('within one session a remount inside five minutes asks for nothing', async () => {
    serve(['a-slack'], ['a-github']);
    renderInTheme(<UniversalAgentResourcesPanel />).unmount();
    await flush();
    renderInTheme(<UniversalAgentResourcesPanel />);
    await flush();

    expect(api.getMyToolsets).toHaveBeenCalledTimes(1);
    expect(mcpApi.getMyMcpServers).toHaveBeenCalledTimes(1);
  });

  it("A's actions answer landing after the sign-out fills neither the panel nor the chat store", async () => {
    serve([], []);
    api.getMyToolsets.mockImplementationOnce(() => viaApiClient());
    const a = renderInTheme(<UniversalAgentResourcesPanel />);
    await flush();
    expect(pending).toHaveLength(1);
    a.unmount();

    signOutAndIn();
    pending[0].answer(200, { toolsets: [toolset('a-slack')], hasNext: false });
    await flush();
    expect(toolLabels()).toEqual([]);

    useFeatureFlagsStore.setState({ flags: { ENABLE_ACTIONS: true, ENABLE_MCP: true } });
    serve(['b-jira']);
    renderInTheme(<UniversalAgentResourcesPanel />);
    await flush();
    expect(toolLabels()).toEqual(['b-jira']);
  });

  it("A's actions failure landing after the sign-out sets no error and leaves no stale cache", async () => {
    serve([], []);
    api.getMyToolsets.mockImplementationOnce(() => viaApiClient());
    const a = renderInTheme(<UniversalAgentResourcesPanel />);
    await flush();
    a.unmount();

    signOutAndIn();
    pending[0].answer(500);
    await flush();
    expect(useChatStore.getState().universalAgentToolsError).toBeNull();
    expect(toast.error).not.toHaveBeenCalled();

    useFeatureFlagsStore.setState({ flags: { ENABLE_ACTIONS: true, ENABLE_MCP: true } });
    serve(['b-jira']);
    renderInTheme(<UniversalAgentResourcesPanel />);
    await flush();
    // Asked again at once: the failure did not mark an empty list as fresh.
    expect(toolLabels()).toEqual(['b-jira']);
  });

  it("A's MCP answer or failure landing after the sign-out is dropped too", async () => {
    serve([], []);
    mcpApi.getMyMcpServers.mockImplementationOnce(() => viaApiClient());
    const a = renderInTheme(<UniversalAgentResourcesPanel />);
    await flush();
    a.unmount();

    signOutAndIn();
    pending[0].answer(200, { instances: [mcpServer('a-github')] });
    await flush();
    expect(mcpLabels()).toEqual([]);
    expect(useChatStore.getState().universalAgentMcpError).toBeNull();

    useFeatureFlagsStore.setState({ flags: { ENABLE_ACTIONS: true, ENABLE_MCP: true } });
    serve([], ['b-linear']);
    mcpApi.getMyMcpServers.mockImplementationOnce(() => viaApiClient());
    const b = renderInTheme(<UniversalAgentResourcesPanel />);
    await flush();
    b.unmount();
    signOutAndIn();
    pending[1].answer(500);
    await flush();
    expect(useChatStore.getState().universalAgentMcpError).toBeNull();

    useFeatureFlagsStore.setState({ flags: { ENABLE_ACTIONS: true, ENABLE_MCP: true } });
    renderInTheme(<UniversalAgentResourcesPanel />);
    await flush();
    expect(mcpLabels()).toEqual(['b-linear']);
  });
});
