/**
 * Past a day, "yesterday" and "N days ago" count the viewer's calendar days, so they agree with the chat list's
 * day groups: 11 PM two days back is "2 days ago" though only 34 hours have passed.
 */
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { formatRelativeTime } from '../formatters';

const original = process.env.TZ;
const at = (iso: string) => Date.parse(iso);
const days = (n: number) => new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' }).format(-n, 'day');

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-23T16:00:00Z')); // 23 Oct 09:00 PDT, 23 Oct 21:30 IST
});
afterEach(() => {
  vi.useRealTimers();
  if (original === undefined) delete process.env.TZ;
  else process.env.TZ = original;
});

describe('formatRelativeTime', () => {
  it('counts calendar days in the viewer zone once a day has passed', () => {
    const lastActive = at('2026-10-22T06:00:00Z'); // 21 Oct 23:00 PDT, 22 Oct 11:30 IST
    process.env.TZ = 'America/Los_Angeles';
    expect(formatRelativeTime(lastActive)).toBe(days(2));
    process.env.TZ = 'Asia/Kolkata';
    expect(formatRelativeTime(lastActive)).toBe(days(1));
  });

  it('keeps hours within a day', () => {
    process.env.TZ = 'America/Los_Angeles';
    expect(formatRelativeTime(at('2026-10-22T17:00:00Z'))).toBe(
      new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' }).format(-23, 'hour'),
    );
  });

  it('never says less than a day back for 24 hours or more', () => {
    process.env.TZ = 'America/Los_Angeles';
    // 1 Nov 00:30 PDT to 1 Nov 23:30 PST is 24 hours on the same 25-hour day.
    vi.setSystemTime(new Date('2026-11-02T07:30:00Z'));
    expect(formatRelativeTime(at('2026-11-01T07:30:00Z'))).toBe(days(1));
  });
});
