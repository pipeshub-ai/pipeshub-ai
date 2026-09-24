/**
 * Parse an environment variable as an integer, returning a fallback if the
 * value is missing or malformed.
 *
 * Rejects values `parseInt` would silently truncate to a numeric prefix.
 * That matters most for `REDIS_STREAMS_MAXLEN`: `"5e5"` used to parse as 5,
 * which would trim the stream to five entries and discard everything else.
 */
export function parseIntSafe(raw: string | undefined, fallback: number): number {
  if (raw === undefined) return fallback;
  const trimmed = raw.trim();
  const parsed = /^[+-]?\d+$/.test(trimmed) ? Number(trimmed) : NaN;
  // isSafeInteger, not just isFinite: a long run of digits becomes Infinity,
  // and anything past 2^53 silently loses precision on the way to a Redis or
  // Kafka setting.
  if (!Number.isSafeInteger(parsed)) {
    console.warn(
      `Ignoring malformed integer value ${JSON.stringify(raw)}; using ${fallback}`,
    );
    return fallback;
  }
  return parsed;
}

/**
 * Parse an environment variable as a whole positive number.
 *
 * Deliberately stricter than `parseIntSafe`: `parseInt` accepts a numeric
 * prefix, so "4x" becomes 4, "8lanes" becomes 8 and "1e3" becomes 1. For
 * counts that provision infrastructure that is awkward or impossible to undo
 * -- Kafka partitions can be increased but never reduced, and Redis lane
 * streams have to match what the consumer subscribes to -- a typo silently
 * resolving to a plausible number is worse than ignoring it.
 *
 * Matches the Python side's `env_int`, which rejects exactly these values and
 * falls back with a warning, so both languages agree on what a malformed
 * count means.
 */
export function parsePositiveIntSafe(
  raw: string | undefined,
  fallback: number,
  name = 'environment variable',
): number {
  if (raw === undefined) return fallback;
  const trimmed = raw.trim();
  if (/^\d+$/.test(trimmed)) {
    const parsed = Number(trimmed);
    // isSafeInteger rejects the Infinity a long digit run produces, which
    // would otherwise satisfy `> 0`, and anything past 2^53 that would lose
    // precision before it reaches a partition or lane count.
    if (Number.isSafeInteger(parsed) && parsed > 0) return parsed;
  }
  console.warn(
    `Ignoring malformed ${name} value ${JSON.stringify(raw)}; using ${fallback}`,
  );
  return fallback;
}
