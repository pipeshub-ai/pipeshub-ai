/**
 * Scopes ticked when the new-token form opens: the ones that only read. Search needs
 * `semantic:write` (the route it calls requires it), so it counts as reading. Scopes that
 * change data, run agents or chat (chat can run connected actions) are left for the person
 * to tick.
 */
export function defaultPatScopes(names: readonly string[]): Set<string> {
  return new Set(
    names.filter(
      (name) =>
        name.endsWith(':read') ||
        name === 'semantic:write' ||
        ['openid', 'profile', 'email'].includes(name)
    )
  );
}
