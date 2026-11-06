/** Today moves on at the viewer's midnight, and after a wake or a focus, without a reload. */
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useLocalDay } from '../use-local-day';

const original = process.env.TZ;
const at = (iso: string) => Date.parse(iso);

beforeEach(() => {
  process.env.TZ = 'America/Los_Angeles';
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
});
afterEach(() => {
  vi.useRealTimers();
  if (original === undefined) delete process.env.TZ;
  else process.env.TZ = original;
});

describe('useLocalDay', () => {
  it("turns at Los Angeles's midnight, not UTC's", () => {
    vi.setSystemTime(new Date('2026-10-22T06:59:59Z')); // 21 Oct 23:59:59 PDT
    const { result } = renderHook(() => useLocalDay());
    expect(result.current).toBe(at('2026-10-21T07:00:00Z'));

    act(() => vi.advanceTimersByTime(1_000));
    expect(result.current).toBe(at('2026-10-22T07:00:00Z'));

    // UTC's midnight (17:00 PDT) is not a day boundary here.
    act(() => vi.advanceTimersByTime(17 * 3_600_000));
    expect(result.current).toBe(at('2026-10-22T07:00:00Z'));
  });

  it('turns again the next night, through a 25-hour fall-back day', () => {
    vi.setSystemTime(new Date('2026-10-31T08:00:00Z')); // 31 Oct 01:00 PDT
    const { result } = renderHook(() => useLocalDay());
    act(() => vi.advanceTimersByTime(23 * 3_600_000)); // 1 Nov 00:00 PDT
    expect(result.current).toBe(at('2026-11-01T07:00:00Z'));
    act(() => vi.advanceTimersByTime(24 * 3_600_000)); // 1 Nov 23:00 PST
    expect(result.current).toBe(at('2026-11-01T07:00:00Z'));
    act(() => vi.advanceTimersByTime(3_600_000)); // 2 Nov 00:00 PST
    expect(result.current).toBe(at('2026-11-02T08:00:00Z'));
  });

  it('catches up when the page comes back after the timer was held', () => {
    vi.setSystemTime(new Date('2026-10-22T20:00:00Z'));
    const { result } = renderHook(() => useLocalDay());
    vi.setSystemTime(new Date('2026-10-24T20:00:00Z'));
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(result.current).toBe(at('2026-10-24T07:00:00Z'));
  });
});
