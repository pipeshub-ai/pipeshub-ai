/** A date and time as the reader's locale writes it; an unreadable value is shown as given. */
export function formatDateTime(value: string | number, language: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  try {
    return new Intl.DateTimeFormat(language, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
  } catch {
    return date.toISOString();
  }
}

/** "12 seconds ago", "in 40 seconds": a time near now as the reader's locale says it. */
export function formatRelative(at: number, now: number, language: string): string {
  const seconds = Math.round((at - now) / 1000);
  const abs = Math.abs(seconds);
  const [value, unit]: [number, Intl.RelativeTimeFormatUnit] =
    abs < 60
      ? [seconds, 'second']
      : abs < 3_600
        ? [Math.round(seconds / 60), 'minute']
        : abs < 86_400
          ? [Math.round(seconds / 3_600), 'hour']
          : [Math.round(seconds / 86_400), 'day'];
  try {
    return new Intl.RelativeTimeFormat(language, { numeric: 'auto' }).format(value, unit);
  } catch {
    return new Intl.RelativeTimeFormat('en', { numeric: 'auto' }).format(value, unit);
  }
}
