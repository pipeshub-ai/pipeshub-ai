import { describe, it, expect, vi, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { usePaginatedList } from '../use-paginated-list';

afterEach(() => {
  vi.useRealTimers();
});

describe('usePaginatedList search', () => {
  it('a search typed just before unmount never fetches', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn().mockResolvedValue({ items: [], totalCount: 0 });
    const { result, unmount } = renderHook(() => usePaginatedList({ fetcher, searchDebounceMs: 300 }));
    await act(async () => {});
    fetcher.mockClear();

    act(() => result.current.setSearch('al'));
    unmount();
    await vi.advanceTimersByTimeAsync(1000);

    expect(fetcher).not.toHaveBeenCalled();
  });

  it('a page still on its way at unmount is dropped', async () => {
    let land: (page: { items: { id: string }[]; totalCount: number }) => void = () => {};
    const fetcher = vi.fn(() => new Promise<{ items: { id: string }[]; totalCount: number }>((resolve) => (land = resolve)));
    const onFetched = vi.fn();
    const { unmount } = renderHook(() => usePaginatedList({ fetcher, onFetched }));
    unmount();
    await act(async () => land({ items: [{ id: 'u1' }], totalCount: 1 }));

    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(onFetched).not.toHaveBeenCalled();
  });

  it('a search fetches once its pause is over', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn().mockResolvedValue({ items: [], totalCount: 0 });
    const { result } = renderHook(() => usePaginatedList({ fetcher, searchDebounceMs: 300 }));
    await act(async () => {});
    fetcher.mockClear();

    act(() => result.current.setSearch('al'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });

    expect(fetcher).toHaveBeenCalledWith('al', 1, expect.any(Number));
  });
});
