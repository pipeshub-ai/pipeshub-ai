export const CANONICAL_ROLES = [
  'none',
  'viewer',
  'commenter',
  'editor',
  'manager',
  'owner',
] as const;

export type CanonicalRole = (typeof CANONICAL_ROLES)[number];

export function rank(role: CanonicalRole): number {
  return CANONICAL_ROLES.indexOf(role);
}

export function atLeast(
  actual: CanonicalRole,
  required: CanonicalRole,
): boolean {
  return rank(actual) >= rank(required);
}

export function maxRole(...roles: readonly CanonicalRole[]): CanonicalRole {
  return roles.reduce<CanonicalRole>(
    (best, r) => (rank(r) > rank(best) ? r : best),
    'none',
  );
}

export function minRole(a: CanonicalRole, b: CanonicalRole): CanonicalRole {
  return rank(a) <= rank(b) ? a : b;
}
