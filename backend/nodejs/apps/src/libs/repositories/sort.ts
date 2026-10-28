/**
 * A sort with the `_id` as its last key, in the direction of its first (descending when it has none): rows with equal
 * values keep one order, so a page boundary between them neither repeats nor skips a row. The PostgreSQL stores break
 * ties the same way.
 */
export function withTiebreak(
  sort: Record<string, 1 | -1>,
): Record<string, 1 | -1> {
  if ('_id' in sort) return sort;
  return { ...sort, _id: Object.values(sort)[0] === 1 ? 1 : -1 };
}
