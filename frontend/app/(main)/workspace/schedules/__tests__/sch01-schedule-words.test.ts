/** ADR-SCH-01 D4: when a schedule runs, in plain words; a cron shape not put in words shows its text "(cron)". */
import { beforeAll, describe, expect, it } from 'vitest';
import type { TFunction } from 'i18next';
import { i18n } from '@/lib/i18n';
import { cronShape, cronWords, onceWords, whenWords } from '../schedule-words';

// The reader's zone, so whether a one-off names its zone is fixed.
process.env.TZ = 'America/Los_Angeles';

let t: TFunction;
beforeAll(async () => {
  await i18n.changeLanguage('en-US');
  t = i18n.t.bind(i18n) as TFunction;
});

/** Intl puts a narrow no-break space before AM/PM; compare as a person reads it. */
const plain = (text: string) => text.replace(/[  ]/g, ' ');

describe('cronWords', () => {
  it.each([
    ['* * * * *', 'Every minute'],
    ['*/5 * * * *', 'Every 5 minutes'],
    ['*/15 * * * *', 'Every 15 minutes'],
    ['7 * * * *', 'Every hour at :07'],
    ['30 8 * * *', 'Every day at 8:30 AM'],
    ['0 9 * * 1-5', 'Every weekday at 9:00 AM'],
    ['0 9 * * MON-FRI', 'Every weekday at 9:00 AM'],
    ['0 17 * * 1', 'Every Monday at 5:00 PM'],
    ['0 17 * * 0', 'Every Sunday at 5:00 PM'],
    ['0 17 * * 7', 'Every Sunday at 5:00 PM'],
    ['15 6 * * fri', 'Every Friday at 6:15 AM'],
    ['0 0 1 * *', 'Every month on day 1 at 12:00 AM'],
    ['0 12 15 * *', 'Every month on day 15 at 12:00 PM'],
  ])('%s → %s', (cron, words) => {
    expect(plain(cronWords(t, cron, 'en-US'))).toBe(words);
  });

  it.each([
    '0 9 1-15 * *',
    '0 9 * 1 *',
    '0 9,17 * * *',
    '0 */2 * * *',
    '0 9 1 * 1',
    '*/0 * * * *',
    '60 9 * * *',
    '0 24 * * *',
    '0 9 * * 1,3',
    '0 9 32 * *',
    '@daily',
    '0 9 * *',
  ])('%s is shown as its text, marked cron', (cron) => {
    expect(cronShape(cron)).toBeNull();
    expect(cronWords(t, cron, 'en-US')).toBe(`${cron} (cron)`);
  });

  it('a cron names its zone', () => {
    expect(plain(whenWords(t, { kind: 'cron', cron: '0 9 * * 1-5', timeZone: 'America/Los_Angeles' }, 'en-US'))).toBe(
      'Every weekday at 9:00 AM (America/Los_Angeles)',
    );
    expect(whenWords(t, { kind: 'cron', cron: '0 9 1-3 * *', timeZone: 'Europe/Berlin' }, 'en-US')).toBe(
      '0 9 1-3 * * (cron) (Europe/Berlin)',
    );
  });

  it('times follow the reader’s locale', () => {
    expect(cronWords(t, '0 17 * * *', 'de-DE')).toContain('17:00');
  });
});

describe('onceWords', () => {
  const now = new Date('2026-11-26T15:42:00-08:00');

  it('today, tomorrow and a later date, in the schedule’s zone', () => {
    expect(plain(onceWords(t, '2026-11-26T15:47:00-08:00', 'America/Los_Angeles', 'en-US', now))).toBe('Once, today at 3:47 PM');
    expect(plain(onceWords(t, '2026-11-27T17:00:00Z', 'America/Los_Angeles', 'en-US', now))).toBe('Once, tomorrow at 9:00 AM');
    expect(plain(onceWords(t, '2026-12-03T17:00:00Z', 'America/Los_Angeles', 'en-US', now))).toBe('Once, Dec 3, 2026, 9:00 AM');
  });

  it('names the zone when it is not the reader’s', () => {
    // Now is already the 27th in Berlin; 23:47 UTC on the 27th is 00:47 there on the 28th: tomorrow there.
    expect(plain(onceWords(t, '2026-11-27T23:47:00Z', 'Europe/Berlin', 'en-US', now))).toBe(
      'Once, tomorrow at 12:47 AM (Europe/Berlin)',
    );
  });

  // Today and tomorrow are calendar dates in the schedule's zone: a DST change makes a day 23 or 25 hours long.
  it.each([
    ['spring forward: the 15th is not tomorrow', '2027-03-13T23:30:00-08:00', '2027-03-15T09:00:00-07:00', 'America/Los_Angeles', 'Once, Mar 15, 2027, 9:00 AM'],
    ['spring forward: the 14th is tomorrow', '2027-03-13T23:30:00-08:00', '2027-03-14T09:00:00-07:00', 'America/Los_Angeles', 'Once, tomorrow at 9:00 AM'],
    ['spring forward: later the same day is today', '2027-03-13T23:30:00-08:00', '2027-03-13T23:45:00-08:00', 'America/Los_Angeles', 'Once, today at 11:45 PM'],
    ['across the gap the same day is today', '2027-03-14T01:30:00-08:00', '2027-03-14T03:15:00-07:00', 'America/Los_Angeles', 'Once, today at 3:15 AM'],
    ['fall back: the 2nd is tomorrow', '2026-11-01T00:30:00-07:00', '2026-11-02T09:00:00-08:00', 'America/Los_Angeles', 'Once, tomorrow at 9:00 AM'],
    ['fall back: the 3rd is not tomorrow', '2026-11-01T00:30:00-07:00', '2026-11-03T00:10:00-08:00', 'America/Los_Angeles', 'Once, Nov 3, 2026, 12:10 AM'],
    ['another zone’s DST: the 29th in Berlin is not tomorrow', '2027-03-27T23:30:00+01:00', '2027-03-29T00:30:00+02:00', 'Europe/Berlin', 'Once, Mar 29, 2027, 12:30 AM (Europe/Berlin)'],
    ['another zone’s DST: the 28th in Berlin is tomorrow', '2027-03-27T23:30:00+01:00', '2027-03-28T23:30:00+02:00', 'Europe/Berlin', 'Once, tomorrow at 11:30 PM (Europe/Berlin)'],
  ])('%s', (_case, now, at, zone, words) => {
    expect(plain(onceWords(t, at, zone, 'en-US', new Date(now)))).toBe(words);
  });

  it('an unknown zone or an unreadable time never throws', () => {
    expect(plain(onceWords(t, '2026-11-26T23:47:00Z', 'Not/AZone', 'en-US', now))).toBe('Once, today at 3:47 PM');
    expect(onceWords(t, 'soon', 'America/Los_Angeles', 'en-US', now)).toBe('Once, soon');
  });
});
