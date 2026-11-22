/**
 * The tool catalog shared by the project composer and the project settings picker:
 * one request for both, reused for five minutes, and gone with the session.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import type { McpMyServerEntry } from '@/app/(main)/workspace/mcp-servers/types';
import type { ProjectDetail } from '@/chat/project-types';

const toolsetsApi = vi.hoisted(() => ({ getAllMyToolsets: vi.fn() }));
const mcpApi = vi.hoisted(() => ({ getMyMcpServers: vi.fn() }));
vi.mock('@/app/(main)/toolsets/api', () => ({ ToolsetsApi: toolsetsApi, MAX_TOOLSETS_LIST_LIMIT: 200 }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: mcpApi }));

import { useChatStore } from '@/chat/store';
import { useAuthStore } from '@/lib/store/auth-store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { clearSessionCache } from '@/lib/api/session-cache';
import { useProjectScopeHydration, useToolCatalog } from '../use-project-scope-hydration';

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
    instanceId: `${name}-1`,
    instanceName: name,
  } as BuilderSidebarToolset;
}

function answer(names: string[]) {
  return { toolsets: names.map(toolset) };
}

/** Labels of the tool groups a catalog holds. */
const labels = (catalog: { toolGroups: Array<{ label: string }> } | undefined) =>
  (catalog?.toolGroups ?? []).map((g) => g.label);

const project = { _id: 'p1', tools: ['slack.run', 'gmail.run'] } as unknown as ProjectDetail;

/** Reads the fields during render: SWR re-renders only for fields the render used. */
function useCatalog(mcpEnabled: boolean) {
  const { data, error } = useToolCatalog(mcpEnabled);
  return { data, error };
}

// SWR's default cache binds the real setTimeout for focus events when it loads.
const realSetTimeout = globalThis.setTimeout;

async function settle(ms = 20) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  clearSessionCache();
  toolsetsApi.getAllMyToolsets.mockReset();
  mcpApi.getMyMcpServers.mockReset().mockResolvedValue({ instances: [] as McpMyServerEntry[] });
  useFeatureFlagsStore.setState({ flags: { ENABLE_MCP: true } });
  useAuthStore.setState({ accessToken: 'a', refreshToken: 'r', isAuthenticated: true });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('useToolCatalog', () => {
  it('serves the project composer and the settings picker with one request', async () => {
    toolsetsApi.getAllMyToolsets.mockResolvedValue(answer(['slack']));
    const composer = renderHook(() => useCatalog(true));
    const picker = renderHook(() => useCatalog(true));
    await settle();

    expect(toolsetsApi.getAllMyToolsets).toHaveBeenCalledTimes(1);
    expect(mcpApi.getMyMcpServers).toHaveBeenCalledTimes(1);
    expect(labels(composer.result.current.data)).toEqual(['slack']);
    expect(labels(picker.result.current.data)).toEqual(['slack']);
  });

  it('reuses the catalog for five minutes, then refetches in the background with the old one on screen', async () => {
    toolsetsApi.getAllMyToolsets.mockResolvedValueOnce(answer(['slack']));
    renderHook(() => useCatalog(true)).unmount();
    await settle();

    await settle(4 * 60_000);
    const inside = renderHook(() => useCatalog(true));
    await settle();
    expect(toolsetsApi.getAllMyToolsets).toHaveBeenCalledTimes(1);
    inside.unmount();

    await settle(60_001);
    let finish!: (value: ReturnType<typeof answer>) => void;
    toolsetsApi.getAllMyToolsets.mockReturnValueOnce(new Promise((resolve) => (finish = resolve)));
    const after = renderHook(() => useCatalog(true));
    await settle();
    expect(toolsetsApi.getAllMyToolsets).toHaveBeenCalledTimes(2);
    expect(labels(after.result.current.data)).toEqual(['slack']);

    await act(async () => finish(answer(['slack', 'gmail'])));
    expect(labels(after.result.current.data)).toEqual(['slack', 'gmail']);
  });

  it('asks again when MCP is turned off, and skips the MCP servers then', async () => {
    toolsetsApi.getAllMyToolsets.mockResolvedValue(answer(['slack']));
    const { rerender } = renderHook(({ mcp }) => useCatalog(mcp), { initialProps: { mcp: true } });
    await settle();

    rerender({ mcp: false });
    await settle();

    expect(toolsetsApi.getAllMyToolsets).toHaveBeenCalledTimes(2);
    expect(mcpApi.getMyMcpServers).toHaveBeenCalledTimes(1);
  });

  it('does not reuse a failure: the next mount asks again at once', async () => {
    toolsetsApi.getAllMyToolsets.mockRejectedValueOnce(new Error('down')).mockResolvedValueOnce(answer(['slack']));
    const first = renderHook(() => useCatalog(true));
    await settle();
    expect(first.result.current.error).toBeTruthy();
    first.unmount();

    const second = renderHook(() => useCatalog(true));
    await settle();
    expect(labels(second.result.current.data)).toEqual(['slack']);
  });

  it('does not retry a failure on a timer', async () => {
    toolsetsApi.getAllMyToolsets.mockRejectedValue(new Error('down'));
    renderHook(() => useCatalog(true));
    await settle();
    await settle(60_000);

    expect(toolsetsApi.getAllMyToolsets).toHaveBeenCalledTimes(1);
  });

  it('does not refetch on focus or reconnect, even after five minutes', async () => {
    toolsetsApi.getAllMyToolsets.mockResolvedValue(answer(['slack']));
    renderHook(() => useCatalog(true));
    await settle();
    await settle(5 * 60_000 + 1);

    await act(async () => {
      window.dispatchEvent(new Event('focus'));
      window.dispatchEvent(new Event('online'));
      await new Promise((r) => realSetTimeout(r, 10));
    });
    await settle();

    expect(toolsetsApi.getAllMyToolsets).toHaveBeenCalledTimes(1);
  });

  it("user A signs out and B signs in without a reload: B's first mount asks again and sees only B's tools", async () => {
    toolsetsApi.getAllMyToolsets.mockResolvedValueOnce(answer(['a-slack'])).mockResolvedValueOnce(answer(['b-jira']));
    renderHook(() => useCatalog(true)).unmount();
    await settle();

    act(() => useAuthStore.getState().logout());
    useAuthStore.setState({ accessToken: 'b', refreshToken: 'rb', isAuthenticated: true });
    const b = renderHook(() => useCatalog(true));
    expect(b.result.current.data).toBeUndefined();
    await settle();

    expect(toolsetsApi.getAllMyToolsets).toHaveBeenCalledTimes(2);
    expect(labels(b.result.current.data)).toEqual(['b-jira']);
  });

  it("does not show A's catalog when it arrives after A signed out", async () => {
    let finishA!: (value: ReturnType<typeof answer>) => void;
    toolsetsApi.getAllMyToolsets
      .mockReturnValueOnce(new Promise((resolve) => (finishA = resolve)))
      .mockResolvedValueOnce(answer(['b-jira']));
    renderHook(() => useCatalog(true)).unmount();
    await settle();

    act(() => useAuthStore.getState().logout());
    const b = renderHook(() => useCatalog(true));
    await settle();
    await act(async () => finishA(answer(['a-slack'])));
    await settle();

    expect(labels(b.result.current.data)).toEqual(['b-jira']);
  });
});

describe('useProjectScopeHydration', () => {
  beforeEach(() => {
    useChatStore.getState().setProjectScope(null);
  });

  it('asks for no catalog on plain chat (no project)', async () => {
    renderHook(() => useProjectScopeHydration(null));
    await settle();

    expect(toolsetsApi.getAllMyToolsets).not.toHaveBeenCalled();
    expect(mcpApi.getMyMcpServers).not.toHaveBeenCalled();
  });

  it('the project chat and the project settings picker still share one request', async () => {
    toolsetsApi.getAllMyToolsets.mockResolvedValue(answer(['slack']));
    renderHook(() => useProjectScopeHydration(project));
    renderHook(() => useCatalog(true));
    await settle();

    expect(toolsetsApi.getAllMyToolsets).toHaveBeenCalledTimes(1);
  });

  it('publishes the knowledge scope at once and the tools when the catalog arrives', async () => {
    let finish!: (value: ReturnType<typeof answer>) => void;
    toolsetsApi.getAllMyToolsets.mockReturnValueOnce(new Promise((resolve) => (finish = resolve)));
    renderHook(() => useProjectScopeHydration(project));
    await settle();

    expect(useChatStore.getState().projectScope?.projectId).toBe('p1');
    expect(useChatStore.getState().projectScope?.toolGroups).toEqual([]);

    await act(async () => finish(answer(['slack'])));
    expect(useChatStore.getState().projectScope?.toolGroups.map((g) => g.label)).toEqual(['slack']);
  });

  it('keeps the knowledge scope with no tools when the catalog fails', async () => {
    toolsetsApi.getAllMyToolsets.mockRejectedValueOnce(new Error('down'));
    renderHook(() => useProjectScopeHydration(project));
    await settle();

    expect(useChatStore.getState().projectScope?.projectId).toBe('p1');
    expect(useChatStore.getState().projectScope?.toolGroups).toEqual([]);
  });

  it('clears the scope for no project and on unmount', async () => {
    toolsetsApi.getAllMyToolsets.mockResolvedValue(answer(['slack']));
    const { rerender, unmount } = renderHook(({ p }) => useProjectScopeHydration(p), {
      initialProps: { p: project as ProjectDetail | null },
    });
    await settle();
    expect(useChatStore.getState().projectScope).not.toBeNull();

    rerender({ p: null });
    expect(useChatStore.getState().projectScope).toBeNull();

    rerender({ p: project });
    await settle();
    unmount();
    expect(useChatStore.getState().projectScope).toBeNull();
  });
});
