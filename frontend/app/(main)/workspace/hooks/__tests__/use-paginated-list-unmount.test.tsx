import { renderHook, act } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { usePaginatedList } from '../use-paginated-list';

describe('usePaginatedList', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('drops a pending debounced search when the component unmounts', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn().mockResolvedValue({ items: [], totalCount: 0 });
    const { result, unmount } = renderHook(() =>
      usePaginatedList({ fetcher, searchDebounceMs: 300 })
    );
    await act(async () => {
      await Promise.resolve();
    });
    const callsAfterInitialLoad = fetcher.mock.calls.length;

    act(() => result.current.setSearch('ali'));
    unmount();
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });

    expect(fetcher).toHaveBeenCalledTimes(callsAfterInitialLoad);
  });
});
