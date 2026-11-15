import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { usePreparingRetry, PREPARING_RETRY_FIRST_MS, PREPARING_RETRY_MAX_MS } from '../use-preparing-retry';

describe('usePreparingRetry', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('asks again while the answer is preparing, waiting twice as long each time up to the cap', () => {
    const { result } = renderHook(() => usePreparingRetry());
    const retry = vi.fn();
    const waits: number[] = [];
    for (let i = 0; i < 6; i += 1) {
      act(() => result.current.settle(true, retry));
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
    const { result } = renderHook(() => usePreparingRetry());
    const retry = vi.fn();
    act(() => result.current.settle(true, retry));
    act(() => result.current.settle(false, retry));
    act(() => vi.advanceTimersByTime(PREPARING_RETRY_MAX_MS * 2));
    expect(retry).not.toHaveBeenCalled();
    act(() => result.current.settle(true, retry));
    act(() => vi.advanceTimersByTime(PREPARING_RETRY_FIRST_MS));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it('asks nothing after the page is gone or the user asked for something else', () => {
    const { result, unmount } = renderHook(() => usePreparingRetry());
    const retry = vi.fn();
    act(() => result.current.settle(true, retry));
    act(() => result.current.reset());
    act(() => vi.advanceTimersByTime(PREPARING_RETRY_MAX_MS));
    act(() => result.current.settle(true, retry));
    unmount();
    act(() => vi.advanceTimersByTime(PREPARING_RETRY_MAX_MS));
    expect(retry).not.toHaveBeenCalled();
  });
});
