import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';
import useSWR from 'swr';

import { create } from 'zustand';
import { createJSONStorage, devtools, persist } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';

import { captureSession, clearSessionCache, failOnSessionEnd, onSessionEnd, resetStoreOnSessionEnd } from '../session-cache';
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

describe('failOnSessionEnd', () => {
  it('settles like the work it wraps', async () => {
    await expect(failOnSessionEnd(Promise.resolve('done'))).resolves.toBe('done');
    await expect(failOnSessionEnd(Promise.reject(new Error('refused')))).rejects.toThrow('refused');
  });

  it('fails with an empty message once the session ends first', async () => {
    const waiting = failOnSessionEnd(new Promise(() => {}));
    clearSessionCache();
    await expect(waiting).rejects.toThrow(/^$/);
  });
});

describe('captureSession', () => {
  it('reads true until the session ends, and a token taken afterwards belongs to the next one', () => {
    const before = captureSession();
    expect(before()).toBe(true);

    clearSessionCache();

    expect(before()).toBe(false);
    expect(captureSession()()).toBe(true);
  });

  it('tells the resets whether the app stays open on another org', () => {
    const reset = vi.fn();
    const stop = onSessionEnd(reset);

    clearSessionCache();
    clearSessionCache({ refetch: true });

    expect(reset.mock.calls).toEqual([[{ refetch: false }], [{ refetch: true }]]);
    stop();
  });

  it('has already moved on when the resets run', () => {
    const before = captureSession();
    let duringReset: (() => boolean) | null = null;
    const stop = onSessionEnd(() => {
      duringReset = captureSession();
    });

    clearSessionCache();

    expect(before()).toBe(false);
    expect(duringReset?.()).toBe(true);
    stop();
  });
});

describe('resetStoreOnSessionEnd', () => {
  interface CounterState {
    count: number;
    extra?: string;
    increment: () => void;
  }

  it('puts a store back to the state it was created with, actions included', () => {
    const useCounter = create<CounterState>((set) => ({ count: 0, increment: () => set((s) => ({ count: s.count + 1 })) }));
    const stop = resetStoreOnSessionEnd(useCounter);
    useCounter.getState().increment();
    useCounter.setState({ extra: 'added later' });

    clearSessionCache();

    expect(useCounter.getState().count).toBe(0);
    expect('extra' in useCounter.getState()).toBe(false);
    useCounter.getState().increment();
    expect(useCounter.getState().count).toBe(1);
    stop();
  });

  it('works through devtools, persist and immer, and rewrites the stored copy', () => {
    const saved = new Map<string, string>();
    const storage = {
      getItem: (k: string) => saved.get(k) ?? null,
      setItem: (k: string, v: string) => void saved.set(k, v),
      removeItem: (k: string) => void saved.delete(k),
    };
    const useProfile = create<{ name: string | null; setName: (n: string) => void }>()(
      devtools(
        persist(
          immer((set) => ({
            name: null,
            setName: (n) =>
              set((s) => {
                s.name = n;
              }),
          })),
          { name: 'test-profile', storage: createJSONStorage(() => storage) },
        ),
      ),
    );
    const stop = resetStoreOnSessionEnd(useProfile);
    useProfile.getState().setName('Ada');
    expect(saved.get('test-profile')).toContain('Ada');

    clearSessionCache();

    expect(useProfile.getState().name).toBeNull();
    expect(saved.get('test-profile')).not.toContain('Ada');
    stop();
  });
});
