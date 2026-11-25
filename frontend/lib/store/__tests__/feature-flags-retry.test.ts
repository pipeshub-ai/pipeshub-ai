/**
 * UIADOPT1-FU-1: a failed flags read (a transient network error on first load) is tried again with backoff, and is
 * never "known": routes that redirect on an off flag wait for a read that answered.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

vi.mock('@/lib/api/axios-instance', () => ({ apiClient: { get: vi.fn() } }));

import { apiClient } from '@/lib/api/axios-instance';
import {
  FLAG_RETRY_DELAYS_MS,
  selectFeatureFlagsKnown,
  selectFeatureFlagsLoaded,
  selectFeatureFlagsReadFailed,
  useFeatureFlagsStore,
} from '../feature-flags-store';

const get = vi.mocked(apiClient.get);
const state = () => useFeatureFlagsStore.getState();
const answer = (featureFlags: Record<string, boolean>) => ({ data: { featureFlags } });

async function advance(ms: number) {
  await vi.advanceTimersByTimeAsync(ms);
}

beforeEach(() => {
  vi.useFakeTimers();
  get.mockReset();
  useFeatureFlagsStore.setState({ flags: null, loading: false, readFailed: false, retrying: false });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('a failed flags read', () => {
  it('is loaded (classic pages carry on) but not known, and is retried until one answers', async () => {
    get.mockRejectedValueOnce(new Error('ERR_NETWORK_CHANGED')).mockResolvedValueOnce(answer({ ENABLE_SKILLS: true }));
    await state().fetchFlags();
    expect(selectFeatureFlagsLoaded(state())).toBe(true);
    expect(selectFeatureFlagsKnown(state())).toBe(false);
    expect(selectFeatureFlagsReadFailed(state())).toBe(false);
    expect(state().retrying).toBe(true);

    await advance(FLAG_RETRY_DELAYS_MS[0]);
    expect(get).toHaveBeenCalledTimes(2);
    expect(selectFeatureFlagsKnown(state())).toBe(true);
    expect(state().flags).toEqual({ ENABLE_SKILLS: true });
    expect(state().retrying).toBe(false);
  });

  it('backs off, and after every retry failed says so; a later read can still answer', async () => {
    get.mockRejectedValue(new Error('offline'));
    await state().fetchFlags();
    for (const delay of FLAG_RETRY_DELAYS_MS) {
      expect(selectFeatureFlagsReadFailed(state())).toBe(false);
      await advance(delay - 1);
      const before = get.mock.calls.length;
      await advance(1);
      expect(get.mock.calls.length).toBe(before + 1);
    }
    expect(get).toHaveBeenCalledTimes(FLAG_RETRY_DELAYS_MS.length + 1);
    expect(selectFeatureFlagsReadFailed(state())).toBe(true);
    expect(selectFeatureFlagsKnown(state())).toBe(false);
    await advance(60_000);
    expect(get).toHaveBeenCalledTimes(FLAG_RETRY_DELAYS_MS.length + 1);

    get.mockResolvedValueOnce(answer({}));
    await state().fetchFlags();
    expect(selectFeatureFlagsKnown(state())).toBe(true);
    expect(selectFeatureFlagsReadFailed(state())).toBe(false);
  });

  it('a later failure keeps the last good answer but is not known until a read answers again', async () => {
    get.mockResolvedValueOnce(answer({ ENABLE_PROJECTS: true })).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(answer({ ENABLE_PROJECTS: true }));
    await state().fetchFlags();
    await state().fetchFlags();
    expect(state().flags).toEqual({ ENABLE_PROJECTS: true });
    expect(selectFeatureFlagsKnown(state())).toBe(false);
    await advance(FLAG_RETRY_DELAYS_MS[0]);
    expect(selectFeatureFlagsKnown(state())).toBe(true);
  });
});
