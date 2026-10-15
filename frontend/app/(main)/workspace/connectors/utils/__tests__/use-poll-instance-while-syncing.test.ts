import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { refresh } = vi.hoisted(() => ({ refresh: vi.fn(() => Promise.resolve()) }));
vi.mock('../refresh-instance-details', () => ({ refreshConnectorInstanceDetails: refresh }));

import { usePollInstanceWhileSyncing } from '../use-poll-instance-while-syncing';

describe('usePollInstanceWhileSyncing', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    refresh.mockClear();
  });
  afterEach(() => vi.useRealTimers());

  it('polls after 5 s and backs off to once a minute', () => {
    renderHook(() => usePollInstanceWhileSyncing('c1', 'QUEUED'));
    vi.advanceTimersByTime(4_999);
    expect(refresh).toHaveBeenCalledTimes(0);
    vi.advanceTimersByTime(1);
    expect(refresh).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(10_000 + 20_000 + 40_000); // 10 s, 20 s, 40 s
    expect(refresh).toHaveBeenCalledTimes(4);
    vi.advanceTimersByTime(60_000);
    expect(refresh).toHaveBeenCalledTimes(5);
  });

  it('does not poll an idle connector and stops when unmounted', () => {
    const idle = renderHook(() => usePollInstanceWhileSyncing('c1', 'IDLE'));
    vi.advanceTimersByTime(120_000);
    expect(refresh).not.toHaveBeenCalled();
    idle.unmount();

    const syncing = renderHook(() => usePollInstanceWhileSyncing('c1', 'SYNCING'));
    syncing.unmount();
    vi.advanceTimersByTime(120_000);
    expect(refresh).not.toHaveBeenCalled();
  });
});
