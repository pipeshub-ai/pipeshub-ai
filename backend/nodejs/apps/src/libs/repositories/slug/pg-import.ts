/**
 * Mongo `counters` documents (`{name, seq}`: the last number handed out under a name) as the per-organisation slug counters and the
 * organisation slug sequence. `identity.slug_counters.next_value` holds the last number used in that organisation, so each
 * organisation continues above the old shared counter (slugs stay unique across organisations, as the global index required) and above
 * every slug it already holds. Run after the users and groups are copied.
 */
import type { Tx as KernelTx } from '../../db';
import { ImportStats, NeutralDoc, emptyStats } from '../import';
import { fill } from '../pg-util';

const KINDS: Record<string, { table: string; pattern: string }> = {
  User: { table: 'identity.users', pattern: '^user-[0-9]+$' },
  UserGroup: { table: 'identity.user_groups', pattern: '^usergroup-[0-9]+$' },
};

export async function importCounterDocs(
  k: KernelTx,
  docs: NeutralDoc[],
): Promise<ImportStats> {
  const stats = emptyStats(docs.length);
  for (const d of docs) {
    const name = typeof d.name === 'string' ? d.name : String(d._id);
    const seq = typeof d.seq === 'number' ? d.seq : 0;
    if (name === 'Org') {
      stats.followUps.push({ kind: 'seed_org_slug_seq', seq });
      continue;
    }
    const kind = KINDS[name];
    if (!kind) {
      stats.skipped.push({
        id: String(d._id),
        reason: `counter '${name}' has no counterpart in PostgreSQL`,
      });
      continue;
    }
    const { rowCount } = await k.query(
      fill(
        `/* pipeshub:import.slug_counters */ INSERT INTO identity.slug_counters (org_id, kind, next_value)
          SELECT o.org_id, $1, GREATEST($2::int, 1000, COALESCE((SELECT max(substring(x.slug from '[0-9]+$')::int) FROM @{T} x WHERE x.org_id = o.org_id AND x.slug ~ $3), 0))
            FROM identity.orgs o ON CONFLICT DO NOTHING`,
        { T: kind.table },
      ),
      [name, seq, kind.pattern],
    );
    stats.inserted['identity.slug_counters'] =
      (stats.inserted['identity.slug_counters'] ?? 0) + (rowCount ?? 0);
  }
  return stats;
}

/**
 * Raises each organisation's counter to at least the highest slug number its users and groups hold. The follower copies users and
 * groups but not the counter changes behind their slugs (counters are per name in MongoDB), so before PostgreSQL becomes the store
 * the counters must catch up, or the next claim (`next_value + 1`) takes a slug already held. Never lowers a counter.
 */
export async function raiseCountersToSlugs(k: KernelTx): Promise<number> {
  let n = 0;
  for (const [name, kind] of Object.entries(KINDS)) {
    const { rowCount } = await k.query(
      fill(
        `/* pipeshub:import.slug_counters_raise */ INSERT INTO identity.slug_counters (org_id, kind, next_value)
          SELECT x.org_id, $1, max(substring(x.slug from '[0-9]+$')::int) FROM @{T} x WHERE x.slug ~ $2 GROUP BY x.org_id
          ON CONFLICT (org_id, kind) DO UPDATE SET next_value = EXCLUDED.next_value WHERE identity.slug_counters.next_value < EXCLUDED.next_value`,
        { T: kind.table },
      ),
      [name, kind.pattern],
    );
    n += rowCount ?? 0;
  }
  return n;
}
