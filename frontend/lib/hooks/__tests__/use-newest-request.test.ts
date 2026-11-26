import { describe, it, expect } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useNewestRequest } from '../use-newest-request';

describe('useNewestRequest', () => {
  it('marks every earlier request stale once a later one begins', () => {
    const { result } = renderHook(() => useNewestRequest());
    const first = result.current();
    expect(first()).toBe(false);
    const second = result.current();
    expect(first()).toBe(true);
    expect(second()).toBe(false);
  });

  it('keeps one counter across renders, so a request begun before a re-render is still compared', () => {
    const { result, rerender } = renderHook(() => useNewestRequest());
    const begin = result.current;
    const first = begin();
    rerender();
    expect(result.current).toBe(begin);
    const second = result.current();
    expect(first()).toBe(true);
    expect(second()).toBe(false);
  });
});
