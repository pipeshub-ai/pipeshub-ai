// The server's own rule (users.routes.ts): one id outside it rejects the whole by-ids lookup.
const LOOKUP_USER_ID = /^[a-fA-F0-9]{24}$/;

/** Whether POST /api/v1/users/by-ids accepts this id. */
export function isLookupUserId(id: string | null | undefined): id is string {
  return typeof id === 'string' && LOOKUP_USER_ID.test(id);
}

/** "Ada Lovelace" → "Ada L"; a single name is kept as is. */
export function shortUserName(fullName: string | null | undefined): string {
  const parts = (fullName ?? '').trim().split(/\s+/).filter(Boolean);
  if (parts.length > 1) return `${parts[0]} ${parts[parts.length - 1][0]}`;
  return parts[0] ?? '';
}

/**
 * The ids worth sending to POST /api/v1/users/by-ids: trimmed, well-formed, duplicates removed.
 * A record with no owner or creator carries an empty id, and the server rejects the whole
 * lookup over it (or over any malformed id), which surfaced as an "Invalid Request" toast.
 */
export function toLookupUserIds(ids: ReadonlyArray<string | null | undefined>): string[] {
  const unique = new Set<string>();
  for (const id of ids) {
    const trimmed = typeof id === 'string' ? id.trim() : '';
    if (isLookupUserId(trimmed)) unique.add(trimmed);
  }
  return Array.from(unique);
}
