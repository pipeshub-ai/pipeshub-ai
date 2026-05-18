/**
 * Drop filter rows that were never configured (empty operator).
 * GitLab datetime filters use `noImplicitOperatorDefault` and may be stored as
 * `{ operator: '', type: 'datetime', value: { start: null, end: null } }`.
 */
export function pruneInactiveFilterValues(
  values: Record<string, unknown> | undefined | null
): Record<string, unknown> {
  if (!values || typeof values !== 'object') return {};
  const out: Record<string, unknown> = {};
  for (const [key, val] of Object.entries(values)) {
    if (!val || typeof val !== 'object' || Array.isArray(val)) continue;
    const row = val as Record<string, unknown>;
    const op = typeof row.operator === 'string' ? row.operator.trim() : '';
    if (!op) continue;
    out[key] = val;
  }
  return out;
}
