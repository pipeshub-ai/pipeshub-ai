import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { useChatStore } from '../store';
import { startRemoteRunPoll } from '../hooks/use-remote-run-poll';
import { ChatApi } from '../api';
import { AgentsApi } from '@/app/(main)/agents/api';

vi.mock('../api', () => ({ ChatApi: { fetchConversation: vi.fn() } }));
vi.mock('@/app/(main)/agents/api', () => ({ AgentsApi: { fetchAgentConversation: vi.fn() } }));

const fetchConversation = vi.mocked(ChatApi.fetchConversation);
const fetchAgentConversation = vi.mocked(AgentsApi.fetchAgentConversation);

const respond = (status: string) => ({ conversation: { status } }) as never;

function remoteRunSlot(convId = 'conv-1') {
  const store = useChatStore.getState();
  const slotId = store.createSlot(convId);
  store.updateSlot(slotId, { isInitialized: true, hasLoaded: true, remoteRunInProgress: true });
  return slotId;
}

const slot = (slotId: string) => useChatStore.getState().slots[slotId];

beforeEach(() => {
  vi.useFakeTimers();
  useChatStore.getState().reset();
  fetchConversation.mockReset();
  fetchAgentConversation.mockReset();
});

afterEach(() => vi.useRealTimers());

describe('startRemoteRunPoll', () => {
  it('keeps polling while the run is in progress, then reloads the conversation once it settles', async () => {
    const slotId = remoteRunSlot();
    fetchConversation
      .mockResolvedValueOnce(respond('Inprogress'))
      .mockResolvedValueOnce(respond('Inprogress'))
      .mockResolvedValueOnce(respond('Complete'));

    startRemoteRunPoll({ slotId, convId: 'conv-1', agentId: null });

    await vi.advanceTimersByTimeAsync(3000);
    await vi.advanceTimersByTimeAsync(3000);
    expect(slot(slotId).isInitialized).toBe(true);
    expect(slot(slotId).remoteRunInProgress).toBe(true);

    await vi.advanceTimersByTimeAsync(3000);

    expect(fetchConversation).toHaveBeenCalledTimes(3);
    expect(slot(slotId).remoteRunInProgress).toBe(false);
    expect(slot(slotId).isInitialized).toBe(false);
  });

  it('reads the status from the agent conversation endpoint for an agent thread', async () => {
    const slotId = remoteRunSlot();
    fetchAgentConversation.mockResolvedValue(respond('Complete'));

    startRemoteRunPoll({ slotId, convId: 'conv-1', agentId: 'agent-1' });
    await vi.advanceTimersByTimeAsync(3000);

    expect(fetchAgentConversation).toHaveBeenCalledWith('agent-1', 'conv-1', { page: 1, limit: 1 });
    expect(fetchConversation).not.toHaveBeenCalled();
  });

  it('retries after a failed status check instead of giving up', async () => {
    const slotId = remoteRunSlot();
    fetchConversation
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce(respond('Complete'));

    startRemoteRunPoll({ slotId, convId: 'conv-1', agentId: null });
    await vi.advanceTimersByTimeAsync(3000);
    expect(slot(slotId).remoteRunInProgress).toBe(true);

    await vi.advanceTimersByTimeAsync(3000);

    expect(slot(slotId).remoteRunInProgress).toBe(false);
    expect(slot(slotId).isInitialized).toBe(false);
  });

  it('pauses without fetching or clearing the flag while a local stream owns the slot', async () => {
    const slotId = remoteRunSlot();
    startRemoteRunPoll({ slotId, convId: 'conv-1', agentId: null });
    useChatStore.getState().updateSlot(slotId, { isStreaming: true });

    await vi.advanceTimersByTimeAsync(3000);

    expect(fetchConversation).not.toHaveBeenCalled();
    expect(slot(slotId).remoteRunInProgress).toBe(true);
  });

  it('gives up after the time cap if the conversation never leaves in-progress', async () => {
    const slotId = remoteRunSlot();
    fetchConversation.mockResolvedValue(respond('Inprogress'));

    startRemoteRunPoll({ slotId, convId: 'conv-1', agentId: null });
    await vi.advanceTimersByTimeAsync(36 * 60 * 1000);

    expect(slot(slotId).remoteRunInProgress).toBe(false);
    expect(slot(slotId).isInitialized).toBe(true);
  });

  it('stops polling when cancelled', async () => {
    const slotId = remoteRunSlot();
    const stop = startRemoteRunPoll({ slotId, convId: 'conv-1', agentId: null });

    stop();
    await vi.advanceTimersByTimeAsync(10_000);

    expect(fetchConversation).not.toHaveBeenCalled();
  });
});
