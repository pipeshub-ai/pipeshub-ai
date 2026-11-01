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

/** The rows a document owns, not the document's own row. */
export async function deleteChildren(
  k: KernelTx,
  m: CollectionMapping,
  ids: string[],
): Promise<void> {
  if (ids.length === 0) return;
  for (const c of m.children ?? [])
    await k.query(
      fill(
        '/* pipeshub:follow.delete_children */ DELETE FROM @{T} WHERE @{P} = ANY($1::text[])',
        { T: ident(c.table), P: ident(c.parentCol) },
      ),
      [ids],
    );
}

/** Children first, then the root row. */
export async function deleteDocs(
  k: KernelTx,
  m: CollectionMapping,
  ids: string[],
): Promise<void> {
  if (ids.length === 0) return;
  for (const c of m.children ?? [])
    await k.query(
      fill(
        '/* pipeshub:follow.delete_children */ DELETE FROM @{T} WHERE @{P} = ANY($1::text[])',
        { T: ident(c.table), P: ident(c.parentCol) },
      ),
      [ids],
    );
  await k.query(
    fill(
      '/* pipeshub:follow.delete_root */ DELETE FROM @{T} WHERE @{I} = ANY($1::text[])',
      { T: ident(m.table), I: ident(m.idCol) },
    ),
    [ids],
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
