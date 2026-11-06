/** A notice's age agrees with the viewer's calendar once it is a day old, as the chat list's "Yesterday" does. */
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { formatRelativeTime } from '../notification-row';

const original = process.env.TZ;
const days = (n: number) => new Intl.RelativeTimeFormat('en-US', { numeric: 'auto' }).format(-n, 'day');

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-23T16:00:00Z')); // 23 Oct 09:00 PDT, 23 Oct 21:30 IST
});
afterEach(() => {
  vi.useRealTimers();
  if (original === undefined) delete process.env.TZ;
  else process.env.TZ = original;
});

describe('notification formatRelativeTime', () => {
  it("says 2 days ago for 11 PM two nights back in Los Angeles, and yesterday in Kolkata", () => {
    const createdAt = '2026-10-22T06:00:00Z'; // 21 Oct 23:00 PDT, 22 Oct 11:30 IST
    process.env.TZ = 'America/Los_Angeles';
    expect(formatRelativeTime(createdAt, 'en-US')).toBe(days(2));
    process.env.TZ = 'Asia/Kolkata';
    expect(formatRelativeTime(createdAt, 'en-US')).toBe(days(1));
  });

  it('keeps hours within a day', () => {
    process.env.TZ = 'America/Los_Angeles';
    expect(formatRelativeTime('2026-10-22T20:00:00Z', 'en-US')).toBe('20 hours ago');
  });
});
