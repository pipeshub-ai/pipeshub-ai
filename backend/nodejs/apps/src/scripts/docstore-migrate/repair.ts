/**
 * Repair from an id-diff: the documents a verify found different, missing or extra are written again from MongoDB (or removed when
 * MongoDB no longer holds them). Used when a change stream cannot be resumed (its token fell out of the oplog) and the copy has to be
 * brought level by comparing instead of replaying. Deletes go children-first (reverse mapping order), writes parents-first.
 */
import { deleteDocs, replaceDocs } from './apply';
import { Log, sourceDb } from './bulk';
import type { DocDiff } from './compare';
import { MAPPINGS } from './mapping';
import { idFilter } from './mongo-read';
import type { MigrationRuntime } from './runtime';

export interface RepairResult {
  replaced: number;
  deleted: number;
  /** Collections whose differences cannot be repaired by id (keyed by something else than `_id`). */
  unrepaired: string[];
}

export async function repair(
  rt: MigrationRuntime,
  diffs: Map<string, DocDiff[]>,
  log: Log,
): Promise<RepairResult> {
  const out: RepairResult = { replaced: 0, deleted: 0, unrepaired: [] };
  const mappings = MAPPINGS.filter((m) => diffs.get(m.collection)?.length);
  for (const m of mappings)
    if (m.sourceKey || m.custom) out.unrepaired.push(m.collection);
  const todo = mappings.filter((m) => !m.sourceKey && !m.custom);
  for (const m of [...todo].reverse()) {
    const ids = (diffs.get(m.collection) ?? [])
      .filter((d) => d.kind === 'extra_in_pg')
      .map((d) => d.id);
    if (!ids.length) continue;
    await rt.jobs((_tx, k) => deleteDocs(k, m, ids));
    out.deleted += ids.length;
  }
  for (const m of todo) {
    const ids = [
      ...new Set(
        (diffs.get(m.collection) ?? [])
          .filter((d) => d.kind !== 'extra_in_pg')
          .map((d) => d.id),
      ),
    ];
    for (let i = 0; i < ids.length; i += rt.cfg.batchSize) {
      const part = ids.slice(i, i + rt.cfg.batchSize);
      const raw = await sourceDb(rt, m)
        .collection(m.collection)
        .find({ _id: { $in: part.map(idFilter) } } as never, {
          promoteLongs: false,
        })
        .toArray();
      await replaceDocs(rt, m, raw);
      out.replaced += raw.length;
      // gone from MongoDB since the verify read it: gone from PostgreSQL too
      const have = new Set(raw.map((r) => String(r._id).toLowerCase()));
      const gone = part.filter((id) => !have.has(id.toLowerCase()));
      if (gone.length) {
        await rt.jobs((_tx, k) => deleteDocs(k, m, gone));
        out.deleted += gone.length;
      }
    }
    log('repaired', { collection: m.collection });
  }
  return out;
}
