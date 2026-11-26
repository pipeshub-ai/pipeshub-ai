/** Rows of a keyed table (a table that keeps no document id) and the key map entries that name them (R45MIGR-FU-4). */
import type { Tx as KernelTx } from '../../libs/db';
import { fill } from '../../libs/repositories/pg-util';
import { keysOf, putKeys, type KeyEntry } from './carry/keymap';
import type { CollectionMapping } from './mapping/types';

const ident = (s: string): string => {
  if (!/^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?$/.test(s))
    throw new Error(`bad identifier '${s}'`);
  return s;
};

/** Removes the row a key map entry names. */
export async function deleteKeyedRow(
  k: KernelTx,
  m: CollectionMapping,
  e: KeyEntry,
): Promise<void> {
  const cols = m.keyed?.columns ?? [];
  await k.query(
    fill('/* pipeshub:follow.delete_keyed */ DELETE FROM @{T} WHERE @{W}', {
      T: ident(m.table),
      W: [
        'org_id = $1',
        ...cols.map((c, i) => ident(c) + ' = $' + String(i + 2)),
      ].join(' AND '),
    }),
    [e.orgId, ...(JSON.parse(e.rowKey) as unknown[])],
  );
}

/**
 * The key map after documents of a keyed table were written: each document's entry is the row it became now, and the row under an
 * older key (the document's thread, bot or email changed) is removed.
 */
export async function recordKeys(
  k: KernelTx,
  m: CollectionMapping,
  docs: Array<Record<string, unknown>>,
  skipped: ReadonlySet<string>,
): Promise<void> {
  if (!m.keyed) return;
  const kept = docs.filter((d) => !skipped.has(String(d._id)));
  const now = await m.keyed.entries(k, kept);
  const before = await keysOf(
    k,
    m.collection,
    now.map((e) => e.sourceId),
  );
  for (const old of before) {
    const cur = now.find((e) => e.sourceId === old.sourceId);
    if (cur && (cur.rowKey !== old.rowKey || cur.orgId !== old.orgId))
      await deleteKeyedRow(k, m, old);
  }
  await putKeys(k, m.collection, now);
}
