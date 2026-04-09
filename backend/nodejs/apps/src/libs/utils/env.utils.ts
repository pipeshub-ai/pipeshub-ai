/**
 * Parse an environment variable as an integer, returning a fallback if the
 * value is missing or not a valid number.
 */
export function parseIntSafe(raw: string | undefined, fallback: number): number {
  if (raw === undefined) return fallback;
  const parsed = parseInt(raw, 10);
  return Number.isNaN(parsed) ? fallback : parsed;
}
