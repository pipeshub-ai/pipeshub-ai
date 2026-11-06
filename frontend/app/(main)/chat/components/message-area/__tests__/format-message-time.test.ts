/** A message's time drops the date only when it is from the viewer's own today, not UTC's. */
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { formatMessageTime } from '../format-message-time';

const original = process.env.TZ;
const clock = (iso: string) => new Date(iso).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-23T02:00:00Z')); // 22 Oct 19:00 PDT, 23 Oct 07:30 IST
});
afterEach(() => {
  vi.useRealTimers();
  if (original === undefined) delete process.env.TZ;
  else process.env.TZ = original;
});

describe('formatMessageTime', () => {
  it("shows only the clock for this afternoon in Los Angeles, after UTC's day has turned", () => {
    process.env.TZ = 'America/Los_Angeles';
    expect(formatMessageTime('2026-10-22T22:00:00Z')).toBe(clock('2026-10-22T22:00:00Z'));
  });

  it("adds the date for last night's message", () => {
    process.env.TZ = 'America/Los_Angeles';
    const text = formatMessageTime('2026-10-22T06:00:00Z'); // 21 Oct 23:00 PDT
    expect(text).not.toBe(clock('2026-10-22T06:00:00Z'));
    expect(text).toMatch(/21/);
  });

  it('reads the day in Kolkata by its own calendar', () => {
    process.env.TZ = 'Asia/Kolkata';
    expect(formatMessageTime('2026-10-22T22:00:00Z')).toBe(clock('2026-10-22T22:00:00Z')); // 23 Oct 03:30 IST
    expect(formatMessageTime('2026-10-22T18:00:00Z')).toMatch(/22/); // 22 Oct 23:30 IST
  });
});
