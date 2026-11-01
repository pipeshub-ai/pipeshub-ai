import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';
import useSWR from 'swr';

import { clearSessionCache, onSessionEnd } from '../session-cache';
import { revalidateSwrNamespace, SWR_NS } from '../swr-cache';

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

  it('keeps a list on screen refreshable by its namespace after an org switch', async () => {
    const fetcher = vi.fn(async () => 'rows');
    renderHook(() => useSWR([SWR_NS.agentLists, 'sidebar'], fetcher).data);
    await settle();

    act(() => clearSessionCache({ refetch: true }));
    await settle();
    expect(fetcher).toHaveBeenCalledTimes(2);

    act(() => revalidateSwrNamespace(SWR_NS.agentLists));
    await settle();
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it('on an org switch, drops the old answer still in flight and shows the new one', async () => {
    const answers: Array<(value: string) => void> = [];
    const onSuccess = vi.fn();
    const { result } = renderHook(() =>
      useSWR(['test/list'], () => new Promise<string>((resolve) => answers.push(resolve)), { onSuccess }).data,
    );
    await settle();

    act(() => clearSessionCache({ refetch: true }));
    await settle();
    await act(async () => answers[0]('old org rows'));
    await act(async () => answers[1]('new org rows'));
    await settle();

    expect(result.current).toBe('new org rows');
    expect(onSuccess).toHaveBeenCalledTimes(1);
    expect(onSuccess).toHaveBeenCalledWith('new org rows', expect.anything(), expect.anything());
  });

  it('on an org switch, forgets an error from before it', async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new Error('old org 500')).mockResolvedValueOnce('new org rows');
    const { result } = renderHook(() => {
      const { data, error } = useSWR(['test/list'], fetcher, { shouldRetryOnError: false });
      return { data, error };
    });
    await settle();
    expect(result.current.error).toBeTruthy();

    act(() => clearSessionCache({ refetch: true }));
    await settle();

    expect(result.current.error).toBeUndefined();
    expect(result.current.data).toBe('new org rows');
  });

  it('resets the stores before any refetch starts, so the refetch belongs to the new session', async () => {
    const order: string[] = [];
    const fetcher = vi.fn(async () => {
      order.push('fetch');
      return 'rows';
    });
    renderHook(() => useSWR(['test/list'], fetcher).data);
    await settle();
    order.length = 0;
    const stop = onSessionEnd(() => order.push('reset'));

    act(() => clearSessionCache({ refetch: true }));
    await settle();

    expect(order).toEqual(['reset', 'fetch']);
    stop();
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
