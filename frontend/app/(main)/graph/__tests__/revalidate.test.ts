import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Revalidator, type VerifyState } from '../engine/revalidate';

function harness(options: { ids?: string[]; call?: (ids: string[], signal?: AbortSignal) => Promise<string[]> } = {}) {
  let now = 1_000;
  const timers: { fn: () => void; at: number; id: number }[] = [];
  let nextId = 1;
  const states: VerifyState[] = [];
  const results: [string[], string[]][] = [];
  const call = vi.fn(options.call ?? (async (ids: string[], _signal?: AbortSignal) => ids));
  const r = new Revalidator({
    getIds: () => options.ids ?? ['block:a', 'block:b'],
    call,
    onResult: (asked, visible) => results.push([asked, visible]),
    onState: (s) => states.push(s),
    now: () => now,
    random: () => 0,
    setTimer: (fn, ms) => {
      const t = { fn, at: now + ms, id: nextId++ };
      timers.push(t);
      return t.id;
    },
    clearTimer: (handle) => {
      const i = timers.findIndex((t) => t.id === handle);
      if (i >= 0) timers.splice(i, 1);
    },
  });
  /** Advance the clock and fire the timers that fall due, letting each settle. */
  const advance = async (ms: number) => {
    const end = now + ms;
    for (;;) {
      timers.sort((a, b) => a.at - b.at);
      const next = timers[0];
      if (!next || next.at > end) break;
      timers.shift();
      now = next.at;
      next.fn();
      // A macrotask turn lets the whole async check (call, catch, finally, reschedule) settle.
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    now = end;
  };
  return { r, call, states, results, advance, timers };
}

describe('Revalidator', () => {
  beforeEach(() => vi.useRealTimers());

  it('checks the ids on screen every 30 seconds and reports who is gone', async () => {
    const h = harness({ call: async () => ['block:a'] });
    h.r.start();
    await h.advance(30_000);
    expect(h.call).toHaveBeenCalledTimes(1);
    expect(h.results[0]).toEqual([['block:a', 'block:b'], ['block:a']]);
    await h.advance(30_000);
    expect(h.call).toHaveBeenCalledTimes(2);
  });

  it('does not check while the tab is hidden, and checks at once when it returns', async () => {
    const h = harness();
    h.r.start();
    h.r.setVisible(false);
    await h.advance(120_000);
    expect(h.call).not.toHaveBeenCalled();
    h.r.setVisible(true);
    await vi.waitFor(() => expect(h.call).toHaveBeenCalledTimes(1));
  });

  it('says nothing is wrong after one failure, warns after two, and blanks after five minutes without a success', async () => {
    const h = harness({ call: async () => Promise.reject(new Error('down')) });
    h.r.start();
    await h.advance(30_000);
    expect(h.states).toEqual([]);
    await h.advance(30_000);
    expect(h.states).toEqual(['degraded']);
    await h.advance(5 * 60_000);
    expect(h.states.at(-1)).toBe('blank');
    expect(h.results).toEqual([]);
  });

  it('recovers: a success after failures returns to ok', async () => {
    let up = false;
    const h = harness({ call: async (ids) => (up ? ids : Promise.reject(new Error('x'))) });
    h.r.start();
    await h.advance(60_000);
    expect(h.states).toEqual(['degraded']);
    up = true;
    await h.advance(30_000);
    expect(h.states).toEqual(['degraded', 'ok']);
  });

  it('splits a very large set into server-sized requests and treats them as one answer', async () => {
    const ids = Array.from({ length: 12_000 }, (_, i) => `block:${i}`);
    const h = harness({ ids, call: async (chunk) => chunk.filter((id) => id !== 'block:7') });
    h.r.start();
    await h.advance(30_000);
    expect(h.call.mock.calls.map(([chunk]) => chunk.length)).toEqual([5000, 5000, 2000]);
    expect(h.results[0][0]).toHaveLength(12_000);
    expect(h.results[0][1]).toHaveLength(11_999);
  });

  it('a failure in any chunk is a failed check, not a partial answer', async () => {
    let n = 0;
    const ids = Array.from({ length: 6_000 }, (_, i) => `block:${i}`);
    const h = harness({ ids, call: async (chunk) => (++n === 2 ? Promise.reject(new Error('x')) : chunk) });
    h.r.start();
    await h.advance(30_000);
    expect(h.results).toEqual([]);
  });

  it('stop cancels the timer', async () => {
    const h = harness();
    h.r.start();
    h.r.stop();
    await h.advance(120_000);
    expect(h.call).not.toHaveBeenCalled();
    expect(h.timers).toHaveLength(0);
  });

  describe('a check still in flight when stop() runs', () => {
    it('reports nothing once it answers, and its request is aborted', async () => {
      let answer!: (ids: string[]) => void;
      let signal: AbortSignal | undefined;
      const h = harness({
        call: (ids, s) => {
          signal = s;
          return new Promise<string[]>((resolve) => (answer = resolve));
        },
      });
      h.r.start();
      await h.advance(30_000); // the check starts and waits
      expect(signal?.aborted).toBe(false);
      h.r.stop();
      expect(signal?.aborted).toBe(true);
      answer(['block:a']);
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(h.results).toEqual([]);
      expect(h.states).toEqual([]);
    });
  });
});
