/**
 * Calendar days in the viewer's zone. Node re-reads the zone when `TZ` is set, as a browser follows the machine's,
 * so each case runs in a real non-UTC zone: Los Angeles (UTC-7 in summer, UTC-8 from 1 Nov 2026) and Kolkata
 * (UTC+5:30, no DST).
 */
import { describe, it, expect, afterEach } from 'vitest';
import { addLocalDays, isSameLocalDay, localDaysBetween, startOfLocalDay } from '../local-day';

const original = process.env.TZ;
const inZone = (zone: string) => {
  process.env.TZ = zone;
};
const at = (iso: string) => Date.parse(iso);

afterEach(() => {
  if (original === undefined) delete process.env.TZ;
  else process.env.TZ = original;
});

describe('startOfLocalDay', () => {
  it("starts the day at the viewer's midnight, not UTC's", () => {
    inZone('America/Los_Angeles');
    expect(startOfLocalDay(at('2026-10-23T02:00:00Z'))).toBe(at('2026-10-22T07:00:00Z'));
    inZone('Asia/Kolkata');
    expect(startOfLocalDay(at('2026-10-23T02:00:00Z'))).toBe(at('2026-10-22T18:30:00Z'));
    inZone('UTC');
    expect(startOfLocalDay(at('2026-10-23T02:00:00Z'))).toBe(at('2026-10-23T00:00:00Z'));
  });

  it('puts the last millisecond before midnight on the day before', () => {
    inZone('America/Los_Angeles');
    expect(startOfLocalDay(at('2026-10-22T06:59:59.999Z'))).toBe(at('2026-10-21T07:00:00Z'));
    expect(startOfLocalDay(at('2026-10-22T07:00:00.000Z'))).toBe(at('2026-10-22T07:00:00Z'));
  });
});

describe('addLocalDays', () => {
  it('steps whole calendar days across the fall-back change, where one day is 25 hours', () => {
    inZone('America/Los_Angeles');
    // 2 Nov 00:30 PST: yesterday (1 Nov) began at 00:00 PDT, 25 hours before today began.
    const now = at('2026-11-02T08:30:00Z');
    expect(addLocalDays(now, 0)).toBe(at('2026-11-02T08:00:00Z'));
    expect(addLocalDays(now, -1)).toBe(at('2026-11-01T07:00:00Z'));
    expect(addLocalDays(now, 1)).toBe(at('2026-11-03T08:00:00Z'));
  });

  it('steps whole calendar days across the spring-forward change, where one day is 23 hours', () => {
    inZone('America/Los_Angeles');
    const now = at('2026-03-09T07:30:00Z'); // 9 Mar 00:30 PDT
    expect(addLocalDays(now, -1)).toBe(at('2026-03-08T08:00:00Z')); // 8 Mar 00:00 PST
  });
});

describe('localDaysBetween', () => {
  it('counts calendar days, not elapsed 24-hour spans', () => {
    inZone('America/Los_Angeles');
    // 11 PM on 21 Oct to 9 AM on 23 Oct: 34 hours, two days back.
    expect(localDaysBetween(at('2026-10-22T06:00:00Z'), at('2026-10-23T16:00:00Z'))).toBe(2);
    // One minute apart, across midnight.
    expect(localDaysBetween(at('2026-10-22T06:59:30Z'), at('2026-10-22T07:00:30Z'))).toBe(1);
  });

  it('gives the same instants a different count in each zone', () => {
    const earlier = at('2026-10-22T18:00:00Z');
    const later = at('2026-10-22T19:00:00Z');
    inZone('Asia/Kolkata'); // 23:30 and 00:30 the next day
    expect(localDaysBetween(earlier, later)).toBe(1);
    inZone('America/Los_Angeles'); // 11:00 and 12:00 the same day
    expect(localDaysBetween(earlier, later)).toBe(0);
  });

  it('stays whole across DST changes', () => {
    inZone('America/Los_Angeles');
    expect(localDaysBetween(at('2026-11-01T06:00:00Z'), at('2026-11-02T08:30:00Z'))).toBe(2);
    expect(localDaysBetween(at('2026-11-01T07:30:00Z'), at('2026-11-02T08:30:00Z'))).toBe(1);
    expect(localDaysBetween(at('2026-03-08T07:59:00Z'), at('2026-03-09T07:30:00Z'))).toBe(2);
  });
});

describe('isSameLocalDay', () => {
  it('reads the day in the viewer zone', () => {
    const a = at('2026-10-22T22:00:00Z');
    const b = at('2026-10-23T02:00:00Z');
    inZone('America/Los_Angeles'); // 15:00 and 19:00 on 22 Oct
    expect(isSameLocalDay(a, b)).toBe(true);
    inZone('UTC');
    expect(isSameLocalDay(a, b)).toBe(false);
  });
});
