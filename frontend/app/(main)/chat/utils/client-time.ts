import dayjs from 'dayjs';

/**
 * IANA timezone name for the user's browser (e.g. "Asia/Kolkata", "America/New_York").
 * Falls back to "UTC" on environments that don't expose Intl timezone info.
 */
export function getClientTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

/**
 * Current wall-clock time in the user's local timezone as ISO-8601 with offset
 * (e.g. "2026-05-05T19:42:13+05:30"). Pairs with the `timezone` field on stream bodies.
 */
export function getClientCurrentTime(): string {
  return dayjs().format();
}
