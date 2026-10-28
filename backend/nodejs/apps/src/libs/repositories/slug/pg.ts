import slugify from 'slug';
import type { Tx as PgKernelTx } from '../../db';

/**
 * The next `<kind>-<n>` slug of this organisation, numbered from 1001 like the shared counter was. The counter row is
 * locked until the unit ends, so two creates never take the same number, and a create that rolls back gives it back.
 */
export async function nextSlug(
  k: PgKernelTx,
  kind: 'User' | 'UserGroup',
): Promise<string> {
  const { rows } = await k.query<{ n: number }>(
    `/* pipeshub:identity.slug_next */ INSERT INTO identity.slug_counters (org_id, kind, next_value) VALUES ($1, $2, 1001)
       ON CONFLICT (org_id, kind) DO UPDATE SET next_value = identity.slug_counters.next_value + 1 RETURNING next_value AS n`,
    [k.orgId, kind],
  );
  return slugify(`${kind}-${String(rows[0]?.n)}`);
}
