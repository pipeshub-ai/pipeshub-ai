/**
 * "Today", "Yesterday" and "Previous 7 Days" follow the viewer's calendar: a chat from a Los Angeles afternoon is
 * Today there all evening, though UTC's day has already turned. `TZ` makes Node use a real zone, as a browser does.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { groupByTime, type TimeGroupKey } from '../group-by-time';

const original = process.env.TZ;
const inZone = (zone: string) => {
  process.env.TZ = zone;
};
const at = (iso: string) => Date.parse(iso);

afterEach(() => {
  vi.useRealTimers();
  if (original === undefined) delete process.env.TZ;
  else process.env.TZ = original;
});

/** The bucket one chat lands in, seen at `now`. */
function bucketOf(chatAt: string, now: string): TimeGroupKey | undefined {
  const groups = groupByTime([at(chatAt)], (ts) => ts, { now: at(now) });
  return (Object.keys(groups) as TimeGroupKey[]).find((key) => groups[key].length > 0);
}

describe('groupByTime in the viewer zone', () => {
  it("keeps a Los Angeles afternoon's chat under Today that evening, after UTC's midnight", () => {
    // 22 Oct 15:00 PDT, seen at 22 Oct 19:00 PDT (already 23 Oct in UTC).
    inZone('America/Los_Angeles');
    expect(bucketOf('2026-10-22T22:00:00Z', '2026-10-23T02:00:00Z')).toBe('Today');
    // 22 Oct 00:30 PDT is still today in Los Angeles; in UTC it is the 22nd, the day before.
    expect(bucketOf('2026-10-22T07:30:00Z', '2026-10-23T02:00:00Z')).toBe('Today');
    expect(bucketOf('2026-10-22T06:00:00Z', '2026-10-23T02:00:00Z')).toBe('Yesterday');
    inZone('UTC');
    expect(bucketOf('2026-10-22T22:00:00Z', '2026-10-23T02:00:00Z')).toBe('Yesterday');
  });

  it('reads the same pair of times in Kolkata by its own day', () => {
    inZone('Asia/Kolkata');
    // 23 Oct 03:30 and 23 Oct 07:30 IST.
    expect(bucketOf('2026-10-22T22:00:00Z', '2026-10-23T02:00:00Z')).toBe('Today');
    // 22 Oct 13:00 IST.
    expect(bucketOf('2026-10-22T07:30:00Z', '2026-10-23T02:00:00Z')).toBe('Yesterday');
  });

  it("moves a chat to Yesterday at Kolkata's midnight, while it is still Today in Los Angeles", () => {
    const chat = '2026-10-22T18:00:00Z'; // 22 Oct 23:30 IST, 22 Oct 11:00 PDT
    const now = '2026-10-22T19:00:00Z'; // 23 Oct 00:30 IST, 22 Oct 12:00 PDT
    inZone('Asia/Kolkata');
    expect(bucketOf(chat, now)).toBe('Yesterday');
    inZone('America/Los_Angeles');
    expect(bucketOf(chat, now)).toBe('Today');
  });

  it('keeps an evening chat under Today two hours later in Los Angeles', () => {
    // 21 Oct 20:00 PDT seen at 21 Oct 22:00 PDT: the same local day (and the same UTC day, 22 Oct).
    inZone('America/Los_Angeles');
    expect(bucketOf('2026-10-22T03:00:00Z', '2026-10-22T05:00:00Z')).toBe('Today');
  });

  it("splits the days at the viewer's midnight to the millisecond", () => {
    inZone('America/Los_Angeles');
    const now = '2026-10-22T07:00:00.000Z'; // 22 Oct 00:00:00 PDT
    expect(bucketOf('2026-10-22T07:00:00.000Z', now)).toBe('Today');
    expect(bucketOf('2026-10-22T06:59:59.999Z', now)).toBe('Yesterday');
    expect(bucketOf('2026-10-21T07:00:00.000Z', now)).toBe('Yesterday');
    expect(bucketOf('2026-10-21T06:59:59.999Z', now)).toBe('Previous 7 Days');
    expect(bucketOf('2026-10-14T07:00:00.000Z', now)).toBe('Previous 7 Days');
    expect(bucketOf('2026-10-14T06:59:59.999Z', now)).toBe('Older');
  });

  it('gives Yesterday all 25 hours of the fall-back day', () => {
    inZone('America/Los_Angeles');
    const now = '2026-11-02T08:30:00Z'; // 2 Nov 00:30 PST
    expect(bucketOf('2026-11-02T08:00:00Z', now)).toBe('Today');
    // 1 Nov 00:30 PDT: 25 hours before today began, still yesterday.
    expect(bucketOf('2026-11-01T07:30:00Z', now)).toBe('Yesterday');
    expect(bucketOf('2026-11-01T06:30:00Z', now)).toBe('Previous 7 Days'); // 31 Oct 23:30 PDT
    expect(bucketOf('2026-10-25T07:00:00Z', now)).toBe('Previous 7 Days'); // 25 Oct 00:00 PDT
    expect(bucketOf('2026-10-25T06:59:59Z', now)).toBe('Older');
  });

  it('gives Yesterday only the 23 hours of the spring-forward day', () => {
    inZone('America/Los_Angeles');
    const now = '2026-03-09T07:30:00Z'; // 9 Mar 00:30 PDT
    expect(bucketOf('2026-03-08T08:00:00Z', now)).toBe('Yesterday'); // 8 Mar 00:00 PST
    expect(bucketOf('2026-03-08T07:59:00Z', now)).toBe('Previous 7 Days'); // 7 Mar 23:59 PST
  });

  it('defaults to the current time', () => {
    inZone('America/Los_Angeles');
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-10-23T02:00:00Z'));
    const groups = groupByTime([at('2026-10-22T22:00:00Z')], (ts) => ts);
    expect(groups.Today).toHaveLength(1);
  });
});
