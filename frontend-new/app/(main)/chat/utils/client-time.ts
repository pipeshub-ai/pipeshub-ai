/**
 * Helpers for deriving the client's IANA timezone and an ISO-8601 timestamp
 * to send alongside chat requests.  Centralised here so every call-site
 * uses the same derivation (and the same fallback when `Intl` is absent).
 */

export function getClientTimezone(): string {
  return typeof Intl !== 'undefined'
    ? Intl.DateTimeFormat().resolvedOptions().timeZone
    : 'UTC';
}

export function getClientCurrentTime(): string {
  return new Date().toISOString();
}
