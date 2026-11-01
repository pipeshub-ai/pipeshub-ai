import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';

vi.mock('@/lib/store/auth-store', () => ({
  useAuthStore: { getState: () => ({ isHydrated: true }) },
  hydrateAuthStore: vi.fn(),
  LOGIN_NAVIGATION_EVENT: 'pipeshub:request-login-navigation',
}));
vi.mock('@/config', () => ({ chatModelOptions: { newChatDefault: () => null } }));
vi.mock('@/lib/api', async (importActual) => ({
  ...(await importActual<typeof import('@/lib/api')>()),
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

import { apiClient, ErrorType } from '@/lib/api';
import { clearSessionCache } from '@/lib/api/session-cache';
import { AgentsApi } from '@/app/(main)/agents/api';
import { fetchModelsForContext } from '@/chat/utils/fetch-models-for-context';
import { useChatStore } from '@/chat/store';
import { useAgentDetail } from '../use-agent-detail';

const api = vi.mocked(apiClient);
const realSetTimeout = globalThis.setTimeout;
const AGENT = {
  _key: 'agent-7',
  name: 'Support Bot',
  toolsets: [],
  models: [{ modelKey: 'm1', modelName: 'gpt-x', provider: 'openai', isDefault: true }],
};
const AGENT_URL = '/api/v1/agents/agent-7';

const agentGets = () => api.get.mock.calls.filter(([url]) => url === AGENT_URL).length;

async function settle(ms = 20) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

/** A component reading the agent the way the chat page and the agent sidebar do. */
const mountReader = (agentId: string | null = 'agent-7') =>
  renderHook(() => {
    const state = useAgentDetail(agentId);
    return { detail: state.detail, unavailable: state.unavailable };
  });

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  clearSessionCache();
  useChatStore.getState().reset();
  api.get.mockResolvedValue({ data: { agent: AGENT } });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('useAgentDetail', () => {
  it('makes one request for every caller on an agent-chat open: page, sidebar and the model loader', async () => {
    const page = mountReader();
    const sidebar = mountReader();
    // The composer's model load, started while the hooks' request is in flight.
    const models = fetchModelsForContext('agent-7');
    await settle();
    await models;

    expect(agentGets()).toBe(1);
    expect(page.result.current.detail?.agent?.name).toBe('Support Bot');
    expect(sidebar.result.current.detail?.agent?.name).toBe('Support Bot');
    expect(useChatStore.getState().settings.availableModels['agent-7']?.models[0]?.modelKey).toBe('m1');
  });

  it('reuses the agent for a remount within 60 s, then refetches in the background', async () => {
    mountReader().unmount();
    await settle(59_000);
    const again = mountReader();
    await settle();
    expect(agentGets()).toBe(1);
    expect(again.result.current.detail?.agent?.name).toBe('Support Bot');
    again.unmount();

    await settle(2_000);
    const later = mountReader();
    expect(later.result.current.detail?.agent?.name).toBe('Support Bot');
    await settle();
    expect(agentGets()).toBe(2);
  });

  it('does not refetch on tab focus', async () => {
    mountReader();
    await settle(61_000);

    await act(async () => {
      window.dispatchEvent(new Event('focus'));
      // SWR's focus listener runs on the real setTimeout it bound when it loaded.
      await new Promise((r) => realSetTimeout(r, 10));
    });
    await settle();

    expect(agentGets()).toBe(1);
  });

  it('makes no request without an agent', async () => {
    mountReader(null);
    await settle();

    expect(api.get).not.toHaveBeenCalled();
  });

  it('asks again on the next mount after a failure, and does not retry on a timer', async () => {
    api.get.mockRejectedValueOnce({ type: ErrorType.SERVER_ERROR, message: 'boom', statusCode: 500 });
    const first = mountReader();
    await settle(30_000);
    expect(first.result.current.unavailable).toBe(true);
    expect(agentGets()).toBe(1);
    first.unmount();

    const next = mountReader();
    await settle();
    expect(agentGets()).toBe(2);
    expect(next.result.current.detail?.agent?.name).toBe('Support Bot');
  });

  it('refetches the open agent once after the user saves it', async () => {
    mountReader();
    await settle();
    api.put.mockResolvedValue({ data: { agent: AGENT } });

    await act(async () => {
      await AgentsApi.updateAgent('agent-7', { name: 'Support Bot' });
    });
    await settle();

    expect(agentGets()).toBe(2);
  });

  it('shows a deleted agent as gone with no request, now and on the next mount', async () => {
    const open = mountReader();
    await settle();
    api.delete.mockResolvedValue({ data: {} });

    await act(async () => {
      await AgentsApi.deleteAgent('agent-7');
    });
    await settle();
    expect(open.result.current.detail?.agent).toBeNull();
    open.unmount();

    const back = mountReader();
    await settle();
    expect(back.result.current.detail?.agent).toBeNull();
    expect(agentGets()).toBe(1);
  });

  it('starts over after sign-out', async () => {
    mountReader().unmount();
    await settle();

    clearSessionCache();
    mountReader();
    await settle();

    expect(agentGets()).toBe(2);
  });

  it('keeps separate entries per agent', async () => {
    mountReader('agent-7');
    mountReader('agent-8');
    await settle();

    expect(api.get).toHaveBeenCalledTimes(2);
  });
});
