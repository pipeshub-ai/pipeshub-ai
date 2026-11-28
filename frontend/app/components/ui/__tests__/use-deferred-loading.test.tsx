import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useDeferredLoading } from '../use-deferred-loading';

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe('useDeferredLoading', () => {
  it('shows nothing for a load faster than the show delay', () => {
    const { result, rerender } = renderHook(({ loading }) => useDeferredLoading(loading), { initialProps: { loading: true } });
    act(() => {
      vi.advanceTimersByTime(149);
    });
    expect(result.current.visible).toBe(false);
    rerender({ loading: false });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(result.current.visible).toBe(false);
  });

  it('once shown, stays at least 300ms, then fades out over 180ms', () => {
    const { result, rerender } = renderHook(({ loading }) => useDeferredLoading(loading), { initialProps: { loading: true } });
    act(() => {
      vi.advanceTimersByTime(150);
    });
    expect(result.current).toEqual({ visible: true, exiting: false });
    rerender({ loading: false });
    act(() => {
      vi.advanceTimersByTime(299);
    });
    expect(result.current).toEqual({ visible: true, exiting: false });
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current).toEqual({ visible: true, exiting: true });
    act(() => {
      vi.advanceTimersByTime(180);
    });
    expect(result.current).toEqual({ visible: false, exiting: false });
  });

  it('a load that resumes during the fade keeps the skeleton up', () => {
    const { result, rerender } = renderHook(({ loading }) => useDeferredLoading(loading), { initialProps: { loading: true } });
    act(() => {
      vi.advanceTimersByTime(600);
    });
    rerender({ loading: false });
    act(() => {
      vi.advanceTimersByTime(50);
    });
    rerender({ loading: true });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(result.current).toEqual({ visible: true, exiting: false });
  });
});
