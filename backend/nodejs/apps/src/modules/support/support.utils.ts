import { randomInt } from 'crypto';

// No 0/O, 1/I/L or U, so a reference read aloud or retyped from a screenshot stays unambiguous.
const REFERENCE_ALPHABET = '23456789ABCDEFGHJKMNPQRSTVWXYZ';
const REFERENCE_LENGTH = 6;

/** A label that ties the emails and the person's follow-ups together; nothing looks it up. */
export function newSupportReference(): string {
  let code = '';
  for (let i = 0; i < REFERENCE_LENGTH; i += 1) {
    code += REFERENCE_ALPHABET.charAt(randomInt(REFERENCE_ALPHABET.length));
  }
  return `SUP-${code}`;
}

/** "Chrome 141 on Windows" from a user-agent header, or "Unknown browser". */
export function describeUserAgent(userAgent?: string): string {
  if (userAgent === undefined || userAgent.length === 0) {
    return 'Unknown browser';
  }
  const match = (pattern: RegExp): string | undefined =>
    pattern.exec(userAgent)?.[1];

  const browsers: Array<[string | undefined, (version: string) => string]> = [
    [match(/Electron\/(\d+)/), () => 'Desktop app'],
    [match(/Edg\/(\d+)/), (version) => `Edge ${version}`],
    [match(/Chrome\/(\d+)/), (version) => `Chrome ${version}`],
    [match(/Firefox\/(\d+)/), (version) => `Firefox ${version}`],
    [
      /Safari\//.test(userAgent) ? match(/Version\/(\d+)/) : undefined,
      (version) => `Safari ${version}`,
    ],
  ];
  const found = browsers.find(([version]) => version !== undefined);
  const browser =
    found?.[0] !== undefined ? found[1](found[0]) : 'Unknown browser';

  const systems: Array<[RegExp, string]> = [
    [/Windows/, 'Windows'],
    [/iPhone|iPad/, 'iOS'],
    [/Mac OS X/, 'macOS'],
    [/Android/, 'Android'],
    [/Linux/, 'Linux'],
  ];
  const os = systems.find(([pattern]) => pattern.test(userAgent))?.[1];

  return os !== undefined ? `${browser} on ${os}` : browser;
}

/** Header values must not carry line breaks: a workspace name with one could inject a header. */
export function toHeaderText(value: string, maxLength = 250): string {
  return value
    .replace(/[\r\n\t\v\f]+/g, ' ')
    .trim()
    .slice(0, maxLength);
}

/** Up to two letters for the avatar shown beside a person's name in an email. */
export function initialsOf(name: string): string {
  const letters = name
    .trim()
    .split(/[\s._@-]+/)
    .filter((part) => part.length > 0)
    .slice(0, 2)
    .map((part) => Array.from(part)[0] ?? '')
    .join('');
  return letters.length > 0 ? letters.toUpperCase() : '?';
}

const UTC_DATE_TIME_FORMAT = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
  timeZone: 'UTC',
});

/** "8 Oct 2026, 21:10 UTC" */
export function formatUtcDateTime(date: Date): string {
  return `${UTC_DATE_TIME_FORMAT.format(date)} UTC`;
}
