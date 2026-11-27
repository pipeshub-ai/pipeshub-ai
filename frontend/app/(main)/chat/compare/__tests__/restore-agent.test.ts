import { describe, it, expect, beforeEach, vi } from 'vitest';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';

installMemoryStorage();

const { useFeatureFlagsStore } = await import('@/lib/store/feature-flags-store');
const { useChatStore } = await import('../../store');
const { CompareApi } = await import('../compare-api');
const { restoreCompare } = await import('../compare-controller');

describe('restoreCompare', () => {
  beforeEach(() => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_CHAT_COMPARE: true } });
    vi.restoreAllMocks();
  });

  const openSlot = (agentId: string | null) => {
    const slotId = useChatStore.getState().createSlot('conv-1');
    useChatStore.getState().updateSlot(slotId, { isInitialized: true, hasLoaded: true, threadAgentId: agentId });
    return slotId;
  };

  it('does not ask for branches of an agent chat, where Compare is never offered', async () => {
    const list = vi.spyOn(CompareApi, 'listBranches').mockResolvedValue([]);
    await restoreCompare(openSlot('agent-1'));
    expect(list).not.toHaveBeenCalled();
  });

  it('still asks for branches of a plain chat', async () => {
    const list = vi.spyOn(CompareApi, 'listBranches').mockResolvedValue([]);
    await restoreCompare(openSlot(null));
    expect(list).toHaveBeenCalledWith('conv-1');
  });
});
