import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { usePreparingRetry, preparingKey, PREPARING_RETRY_FIRST_MS, PREPARING_RETRY_MAX_MS } from '../use-preparing-retry';

const A = 'collections:app:a';
const B = 'collections:app:b';

describe('usePreparingRetry', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('asks again while the answer is preparing, waiting twice as long each time up to the cap', () => {
    const { result } = renderHook(() => usePreparingRetry(A));
    const retry = vi.fn();
    const waits: number[] = [];
    for (let i = 0; i < 6; i += 1) {
      act(() => result.current.settle(A, true, retry));
      const before = retry.mock.calls.length;
      let waited = 0;
      while (retry.mock.calls.length === before) {
        act(() => vi.advanceTimersByTime(1_000));
        waited += 1_000;
      }
      waits.push(waited);
    }
    expect(waits).toEqual([2_000, 4_000, 8_000, 16_000, PREPARING_RETRY_MAX_MS, PREPARING_RETRY_MAX_MS]);
  });

  it('stops once an answer is no longer preparing, and starts the waits over after that', () => {
    const { result } = renderHook(() => usePreparingRetry(A));
    const retry = vi.fn();
    act(() => result.current.settle(A, true, retry));
    act(() => result.current.settle(A, false, retry));
    act(() => vi.advanceTimersByTime(PREPARING_RETRY_MAX_MS * 2));
    expect(retry).not.toHaveBeenCalled();
    act(() => result.current.settle(A, true, retry));
    act(() => vi.advanceTimersByTime(PREPARING_RETRY_FIRST_MS));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it('cancels the retry when the listing on screen changes, and an answer for a listing left behind starts none', () => {
    const { result, rerender } = renderHook(({ key }) => usePreparingRetry(key), { initialProps: { key: A as string | null } });
    const retry = vi.fn();
    act(() => result.current.settle(A, true, retry));
    rerender({ key: B });
    act(() => vi.advanceTimersByTime(PREPARING_RETRY_MAX_MS));
    expect(retry).not.toHaveBeenCalled();
    expect(result.current.isCurrent(A)).toBe(false);
    act(() => result.current.settle(A, true, retry));      // a late answer for A
    act(() => vi.advanceTimersByTime(PREPARING_RETRY_MAX_MS));
    expect(retry).not.toHaveBeenCalled();
    rerender({ key: null });      // no listing that waits on a source
    act(() => result.current.settle(B, true, retry));
    act(() => vi.advanceTimersByTime(PREPARING_RETRY_MAX_MS));
    expect(retry).not.toHaveBeenCalled();
  });

  it('asks nothing after the page is gone', () => {
    const { result, unmount } = renderHook(() => usePreparingRetry(A));
    const retry = vi.fn();
    act(() => result.current.settle(A, true, retry));
    unmount();
    act(() => vi.advanceTimersByTime(PREPARING_RETRY_MAX_MS));
    expect(retry).not.toHaveBeenCalled();
  });

  it('takes no answer and starts no timer for a retry that comes back after the page is gone', () => {
    const { result, unmount } = renderHook(() => usePreparingRetry(A));
    const retry = vi.fn();
    act(() => result.current.settle(A, true, retry));
    act(() => vi.advanceTimersByTime(PREPARING_RETRY_FIRST_MS));      // the retry is sent
    expect(retry).toHaveBeenCalledTimes(1);
    const { settle, isCurrent } = result.current;
    unmount();      // and its answer comes back after this
    expect(isCurrent(A)).toBe(false);
    act(() => settle(A, true, retry));
    act(() => vi.advanceTimersByTime(PREPARING_RETRY_MAX_MS * 2));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it('keys a listing by its view and node', () => {
    expect(preparingKey(false, 'app', 'kb-1')).toBe('collections:app:kb-1');
    expect(preparingKey(false, null, null)).toBeNull();
    expect(preparingKey(true, null, null)).toBe('all-records::');
    expect(preparingKey(true, 'app', 'kb-1')).not.toBe(preparingKey(false, 'app', 'kb-1'));
  });
});
