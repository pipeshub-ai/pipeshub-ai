import { describe, it, expect, beforeEach } from 'vitest';
import { useChatStore } from '../store';

function loadedSlot(convId: string, patch: Parameters<ReturnType<typeof useChatStore.getState>['updateSlot']>[1] = {}) {
  const store = useChatStore.getState();
  const slotId = store.createSlot(convId);
  store.updateSlot(slotId, { isInitialized: true, hasLoaded: true, ...patch });
  return slotId;
}

const slot = (slotId: string) => useChatStore.getState().slots[slotId];

beforeEach(() => useChatStore.getState().reset());

describe('invalidateConversation', () => {
  it('makes every loaded slot of the conversation load again, and leaves other conversations', () => {
    const open = loadedSlot('conv-1');
    const agentThread = loadedSlot('conv-1', { threadAgentId: 'agent-1' });
    const other = loadedSlot('conv-2');

    useChatStore.getState().invalidateConversation('conv-1');

    expect(slot(open).isInitialized).toBe(false);
    expect(slot(agentThread).isInitialized).toBe(false);
    expect(slot(other).isInitialized).toBe(true);
  });

  it('keeps a slot that is streaming or stopping as it is, so a live answer is not dropped', () => {
    const streaming = loadedSlot('conv-1', { isStreaming: true });
    const stopping = loadedSlot('conv-1', { stopping: true });

    useChatStore.getState().invalidateConversation('conv-1');

    expect(slot(streaming).isInitialized).toBe(true);
    expect(slot(stopping).isInitialized).toBe(true);
  });

  it('counts an invalidation that arrives while the history is still loading', () => {
    const store = useChatStore.getState();
    const loading = store.createSlot('conv-1');
    const before = slot(loading).refreshGeneration;

    useChatStore.getState().invalidateConversation('conv-1');

    expect(slot(loading).isInitialized).toBe(false);
    expect(slot(loading).refreshGeneration).toBe(before + 1);
  });

  it('does not count an invalidation for a streaming slot', () => {
    const streaming = loadedSlot('conv-1', { isStreaming: true });
    const before = slot(streaming).refreshGeneration;

    useChatStore.getState().invalidateConversation('conv-1');

    expect(slot(streaming).refreshGeneration).toBe(before);
  });

  it('changes nothing when no slot holds the conversation', () => {
    loadedSlot('conv-2');
    const before = useChatStore.getState().slots;

    useChatStore.getState().invalidateConversation('conv-1');

    expect(useChatStore.getState().slots).toBe(before);
  });
});
