/**
 * Past `MAX_SLOTS`, opening a chat drops the least recently used idle slot. The active slot, the new
 * one and any slot shown beside the active one (`chatExtension.slot.isOnScreen`) are never dropped.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// `../store` pulls in `@/lib/api`, whose auth store reads localStorage at import time; nothing here makes a request.
vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const { useChatStore } = await import('../store');
const { chatExtension } = await import('@/config');
const { MAX_SLOTS } = await import('../types');

const initialState = useChatStore.getState();

/** `MAX_SLOTS` slots, `conv-0` the least recently used; the newest is active. */
function fillSlots(): string[] {
  const ids: string[] = [];
  for (let i = 0; i < MAX_SLOTS; i++) {
    const id = useChatStore.getState().createSlot(`conv-${i}`);
    useChatStore.getState().updateSlot(id, { lastAccessedAt: 1_000 + i });
    ids.push(id);
  }
  useChatStore.getState().setActiveSlot(ids[ids.length - 1]);
  return ids;
}

const convIds = () => Object.values(useChatStore.getState().slots).map((s) => s.convId);

beforeEach(() => {
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('slot eviction', () => {
  it('drops the least recently used idle slot when one more chat opens', () => {
    const ids = fillSlots();

    const opened = useChatStore.getState().createSlot('conv-new');

    expect(Object.keys(useChatStore.getState().slots)).toHaveLength(MAX_SLOTS);
    expect(convIds()).not.toContain('conv-0');
    expect(useChatStore.getState().slots[opened]).toBeDefined();
    expect(useChatStore.getState().slots[ids[ids.length - 1]]).toBeDefined();
  });

  it('never drops a slot shown beside the active one', () => {
    fillSlots();
    const isOnScreen = vi
      .spyOn(chatExtension.slot, 'isOnScreen')
      .mockImplementation((slot, activeSlotId) => slot.convId === 'conv-0' && activeSlotId !== null);

    useChatStore.getState().createSlot('conv-new');

    expect(isOnScreen).toHaveBeenCalled();
    expect(convIds()).toContain('conv-0');
    expect(convIds()).not.toContain('conv-1');
  });

  it('keeps every slot on screen even past the limit', () => {
    fillSlots();
    vi.spyOn(chatExtension.slot, 'isOnScreen').mockReturnValue(true);

    useChatStore.getState().createSlot('conv-new');

    expect(Object.keys(useChatStore.getState().slots)).toHaveLength(MAX_SLOTS + 1);
  });
});
