/** A load of the open chat's history that failed is tried again by itself: how long it waits, and what brings it forward. */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';
import { useChatStore } from '../../store';
import { useLoadRetry } from '../use-load-retry';

const slot = (id: string) => useChatStore.getState().slots[id];
const update = (id: string, patch: Parameters<ReturnType<typeof useChatStore.getState>['updateSlot']>[1]) =>
  act(() => useChatStore.getState().updateSlot(id, patch));
const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const tryingOf = (id: string) => slot(id).failedLoad?.trying === true;
/** The load under way fails, for the `count`th time in a row. */
const fail = (id: string, count: number) => update(id, { isInitialized: true, failedLoad: { count, at: Date.now() } });

function openSlot() {
  const id = useChatStore.getState().createSlot('conv-1');
  useChatStore.getState().updateSlot(id, { isInitialized: true });
  return id;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(Math, 'random').mockReturnValue(0.5);
  useChatStore.getState().reset();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('useLoadRetry', () => {
  it('does nothing for a chat whose load did not fail', async () => {
    const id = openSlot();
    renderHook(() => useLoadRetry(id));
    await advance(120_000);
    window.dispatchEvent(new Event('focus'));
    await advance(1_000);
    expect(slot(id).failedLoad ?? null).toBeNull();
  });

  it('tries after 1, 3, 6, 15 and 30 s, then every 30 s, never more than one at a time', async () => {
    const id = openSlot();
    renderHook(() => useLoadRetry(id));
    for (const [i, wait] of [1_000, 3_000, 6_000, 15_000, 30_000, 30_000].entries()) {
      fail(id, i + 1);
      await advance(wait - 1);
      expect(tryingOf(id)).toBe(false);
      await advance(1);
      expect(tryingOf(id)).toBe(true);
      // Under way: however long it takes, and whatever happens meanwhile, it is the one try.
      const underWay = slot(id).failedLoad;
      window.dispatchEvent(new Event('focus'));
      await advance(60_000);
      expect(slot(id).failedLoad).toBe(underWay);
    }
  });

  it('spreads the wait, so tabs that failed together do not try together', async () => {
    const id = openSlot();
    renderHook(() => useLoadRetry(id));
    vi.spyOn(Math, 'random').mockReturnValue(0);
    fail(id, 2);
    await advance(2_399);
    expect(tryingOf(id)).toBe(false);
    await advance(1);
    expect(tryingOf(id)).toBe(true);
  });

  it.each(['focus', 'online'])('%s tries at once, however long the wait was, but not within a second of the failure', async (event) => {
    const id = openSlot();
    renderHook(() => useLoadRetry(id));
    fail(id, 5);
    await advance(10_000);
    act(() => { window.dispatchEvent(new Event(event)); });
    await advance(0);
    expect(tryingOf(id)).toBe(true);

    fail(id, 6);
    await advance(200);
    for (let i = 0; i < 5; i += 1) act(() => { window.dispatchEvent(new Event(event)); });
    await advance(799);
    expect(tryingOf(id)).toBe(false);
    await advance(1);
    expect(tryingOf(id)).toBe(true);
  });

  it('waits for the answer written here to end, then tries at once', async () => {
    const id = openSlot();
    renderHook(() => useLoadRetry(id));
    fail(id, 1);
    update(id, { isStreaming: true });
    await advance(20_000);
    act(() => { window.dispatchEvent(new Event('online')); });
    await advance(1_000);
    expect(tryingOf(id)).toBe(false);
    update(id, { isStreaming: false });
    await advance(0);
    expect(tryingOf(id)).toBe(true);
  });

  it('leaves a load under way alone (an invalidation)', async () => {
    const id = openSlot();
    renderHook(() => useLoadRetry(id));
    fail(id, 1);
    update(id, { isInitialized: false });
    await advance(30_000);
    expect(tryingOf(id)).toBe(false);
  });

  it('tries on coming back to the chat, and nothing while another one is open', async () => {
    const id = openSlot();
    const other = openSlot();
    const { rerender } = renderHook(({ open }) => useLoadRetry(open), { initialProps: { open: id } });
    fail(id, 1);
    rerender({ open: other });
    await advance(60_000);
    expect(tryingOf(id)).toBe(false);
    rerender({ open: id });
    await advance(0);
    expect(tryingOf(id)).toBe(true);
  });
});
