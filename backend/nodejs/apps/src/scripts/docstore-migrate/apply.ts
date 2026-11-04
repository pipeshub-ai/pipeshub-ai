/** Replacing and deleting whole documents in PostgreSQL (follow mode): the mapping says which tables hold a document. */
import type { Tx as KernelTx } from '../../libs/db';
import { withUpsert } from '../../libs/repositories/import';
import { fill } from '../../libs/repositories/pg-util';
import type { Tx } from '../../libs/repositories/uow';
import { runFollowUps } from './follow-ups';
import type { CollectionMapping } from './mapping/types';
import { repositoryFor } from './bulk';
import type { MigrationRuntime } from './runtime';
import { SourceReport, toNeutral } from './source';

const IDENT = /^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$/;
const ident = (s: string): string => {
  if (!IDENT.test(s) && !/^[a-z_][a-z0-9_]*$/.test(s))
    throw new Error(`bad identifier '${s}'`);
  return s;
};

/** The table keys of source ids (they differ for the outbox, whose key is a number folded from the ObjectId). */
const tableIds = (m: CollectionMapping, ids: string[]): string[] =>
  m.tableId ? ids.map(m.tableId) : ids;

/** The rows a document owns, not the document's own row. */
export async function deleteChildren(
  k: KernelTx,
  m: CollectionMapping,
  sourceIds: string[],
): Promise<void> {
  if (sourceIds.length === 0) return;
  const ids = tableIds(m, sourceIds);
  for (const c of m.children ?? [])
    await k.query(
      fill(
        '/* pipeshub:follow.delete_children */ DELETE FROM @{T} WHERE @{P} = ANY($1::text[])',
        { T: ident(c.table), P: ident(c.parentCol) },
      ),
      [ids],
    );
}

/**
 * A delete event carries only the document's `_id`. Where the table key folds that id and is unique only together with the
 * organisation (the outbox), several organisations can hold a row under it; which one the event meant is not known, so nothing is
 * deleted and the operator decides.
 */
async function refuseAmbiguous(
  k: KernelTx,
  m: CollectionMapping,
  ids: string[],
): Promise<void> {
  if (!m.tableId || !m.orgCol) return;
  const { rows } = await k.query<{ id: string; orgs: string[] }>(
    fill(
      '/* pipeshub:follow.delete_holders */ SELECT @{I}::text AS id, array_agg(@{O} ORDER BY @{O}) AS orgs FROM @{T} WHERE @{I} = ANY($1::@{Y}[]) GROUP BY @{I}',
      {
        T: ident(m.table),
        I: ident(m.idCol),
        O: ident(m.orgCol),
        Y: m.idType ?? 'text',
      },
    ),
    [tableIds(m, ids)],
  );
  const clash = rows.find((r) => r.orgs.length > 1);
  if (clash)
    throw new Error(
      `the delete of ${ids.find((i) => m.tableId?.(i) === clash.id) ?? clash.id} was not applied: key ${clash.id} of ${m.table} is held by more than one organisation (${clash.orgs.join(', ')}) and the event does not say which one it meant; both rows are kept, remove the one that is gone and run follow again`,
    );
}

/** Children first, then the root row. */
export async function deleteDocs(
  k: KernelTx,
  m: CollectionMapping,
  ids: string[],
): Promise<void> {
  if (ids.length === 0) return;
  await refuseAmbiguous(k, m, ids);
  await deleteChildren(k, m, ids);
  await k.query(
    fill(
      '/* pipeshub:follow.delete_root */ DELETE FROM @{T} WHERE @{I} = ANY($1::@{Y}[])',
      { T: ident(m.table), I: ident(m.idCol), Y: m.idType ?? 'text' },
    ),
    [tableIds(m, ids)],
  );
}

/**
 * One unit: the documents are written from the documents given, so a replay converges. The document's own row is updated in place and
 * the rows it owns are replaced: removing the row would also remove the rows of other documents that reference it (the messages of a
 * chat). Two documents that swap a unique value in one batch cannot be updated in place; that batch falls back to remove and insert.
 */
export async function replaceDocs(
  rt: MigrationRuntime,
  m: CollectionMapping,
  raw: unknown[],
): Promise<void> {
  if (raw.length === 0) return;
  const rep = new SourceReport();
  const docs = raw.map((d) => toNeutral(d, rep, m.collection));
  const ids = docs.map((d) => String(d._id));
  let followUps: Array<{ kind: string }> = [];
  const repo = repositoryFor(m);
  const write = async (inPlace: boolean): Promise<void> => {
    await rt.jobs(async (tx: Tx, k) => {
      if (inPlace) await deleteChildren(k, m, ids);
      else await deleteDocs(k, m, ids);
      followUps = (
        await withUpsert(inPlace ? [m.table] : [], () =>
          repo.importBatch(m.collection, docs, tx),
        )
      ).followUps;
    });
  };
  try {
    await write(true);
  } catch (e) {
    // a unique value another document holds for a moment (two documents swapping an address); the database error is mapped by the unit
    if (
      (e as { code?: string }).code !== '23505' &&
      (e as { name?: string }).name !== 'UniqueConstraintError'
    )
      throw e;
    await write(false);
  }
  await runFollowUps(rt, followUps);
}
