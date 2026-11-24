/**
 * With HIDE_EMAIL=true no read of other users carries their email: the single read, the
 * directory list and the bulk lookup alike, or the list would give away what the single read hides.
 */
export function withoutHiddenEmail<T extends object>(user: T): T {
  if (process.env.HIDE_EMAIL !== 'true') return user;
  const { email: _email, ...rest } = user as T & { email?: unknown };
  return rest as T;
}
