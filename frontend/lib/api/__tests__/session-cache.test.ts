import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';
import useSWR from 'swr';

import { clearSessionCache, onSessionEnd } from '../session-cache';

async function settle(ms = 20) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe('clearSessionCache', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    clearSessionCache();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('drops cached responses: a mounted hook shows nothing and does not refetch', async () => {
    const fetcher = vi.fn(async () => 'rows');
    // Read `data` during render: SWR re-renders only for fields the render used.
    const { result } = renderHook(() => useSWR(['test/list'], fetcher).data);
    await settle();
    expect(result.current).toBe('rows');

    act(() => clearSessionCache());
    await settle();

    expect(result.current).toBeUndefined();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('refetches what is on screen when asked to, as after a switch to another org', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce('org A rows').mockResolvedValueOnce('org B rows');
    const { result } = renderHook(() => useSWR(['test/list'], fetcher).data);
    await settle();

    act(() => clearSessionCache({ refetch: true }));
    await settle();

    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(result.current).toBe('org B rows');
  });

  it('discards a response that was still in flight', async () => {
    let answer!: (value: string) => void;
    const onSuccess = vi.fn();
    const { result } = renderHook(() =>
      useSWR(['test/list'], () => new Promise<string>((resolve) => (answer = resolve)), { onSuccess }).data,
    );
    await settle();

    act(() => clearSessionCache());
    await act(async () => answer('rows of the last user'));
    await settle();

    expect(result.current).toBeUndefined();
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it('runs every registered reset, and no longer runs one that was unregistered', () => {
    const kept = vi.fn();
    const dropped = vi.fn();
    const stopKept = onSessionEnd(kept);
    const stopDropped = onSessionEnd(dropped);
    stopDropped();

    clearSessionCache();

    expect(kept).toHaveBeenCalledTimes(1);
    expect(dropped).not.toHaveBeenCalled();
    stopKept();
  });

  it('keeps going when one reset throws, so sign-out always completes', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const after = vi.fn();
    const stopBroken = onSessionEnd(() => {
      throw new Error('broken');
    });
    const stopAfter = onSessionEnd(after);

    expect(() => clearSessionCache()).not.toThrow();
    expect(after).toHaveBeenCalledTimes(1);
    stopBroken();
    stopAfter();
  });
});
