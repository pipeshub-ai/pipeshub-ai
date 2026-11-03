/**
 * The ids worth sending to POST /api/v1/users/by-ids: blanks dropped, duplicates removed.
 * A record with no owner or creator carries an empty id, and the server rejects the whole
 * lookup over it, which surfaced as an "Invalid Request" toast.
 */
export function toLookupUserIds(ids: ReadonlyArray<string | null | undefined>): string[] {
  const unique = new Set<string>();
  for (const id of ids) {
    const trimmed = typeof id === 'string' ? id.trim() : '';
    if (trimmed) unique.add(trimmed);
  }
  return Array.from(unique);
}
