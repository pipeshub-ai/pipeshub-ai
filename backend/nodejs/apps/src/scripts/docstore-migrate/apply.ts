/** Replacing and deleting whole documents in PostgreSQL (follow mode): the mapping says which tables hold a document. */
import type { Tx as KernelTx } from '../../libs/db';
import { ObjectId } from 'mongodb';
import { withUpsert } from '../../libs/repositories/import';
import { fill } from '../../libs/repositories/pg-util';
import type { Tx } from '../../libs/repositories/uow';
import { getPath } from './canonical';
import { runFollowUps } from './follow-ups';
import type { CollectionMapping } from './mapping/types';
import { repositoryFor, sourceDb } from './bulk';
import type { MigrationRuntime } from './runtime';
import { SourceReport, toCanonical, toNeutral } from './source';

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

/** A table whose key folds the source id and is unique only with the organisation (the outbox): an id alone can name another organisation's row. */
const folds = (m: CollectionMapping): boolean => Boolean(m.tableId && m.orgCol);

/** The organisation a source document is stored under, as the copy derives it (the outbox reads it from the event); null when it names none. */
export function orgOfSource(m: CollectionMapping, raw: unknown): string | null {
  if (!m.orgPath) return null;
  const doc = toCanonical(raw, new SourceReport(), m.collection);
  for (const t of m.transforms ?? []) t.apply(doc);
  const org = getPath(doc, m.orgPath);
  return typeof org === 'string' && org !== '' ? org.toLowerCase() : null;
}

/**
 * Where a folded key is concerned, which organisations' rows to delete: `org` names the organisation of a document whose state is
 * known (one the copy now skips); `keep` lists, for a delete event (which carries only the id), the organisations whose row under the
 * same key belongs to another document that is still in MongoDB.
 */
export interface DeleteScope {
  org?: Map<string, string>;
  keep?: Map<string, string[]>;
}

/** Children first, then the root row. */
export async function deleteDocs(
  k: KernelTx,
  m: CollectionMapping,
  ids: string[],
  scope: DeleteScope = {},
): Promise<void> {
  if (ids.length === 0) return;
  const unscoped = ids.filter((id) => !scope.org?.has(id));
  if (unscoped.length) await refuseAmbiguous(k, m, unscoped);
  await deleteChildren(k, m, ids);
  if (!folds(m)) {
    await k.query(
      fill(
        '/* pipeshub:follow.delete_root */ DELETE FROM @{T} WHERE @{I} = ANY($1::@{Y}[])',
        { T: ident(m.table), I: ident(m.idCol), Y: m.idType ?? 'text' },
      ),
      [tableIds(m, ids)],
    );
    return;
  }
  const macros = {
    T: ident(m.table),
    I: ident(m.idCol),
    O: ident(m.orgCol as string),
    Y: m.idType ?? 'text',
  };
  for (const id of ids) {
    const org = scope.org?.get(id);
    await k.query(
      fill(
        org !== undefined
          ? '/* pipeshub:follow.delete_root_of_org */ DELETE FROM @{T} WHERE @{I} = $1::@{Y} AND @{O} = $2'
          : '/* pipeshub:follow.delete_root_folded */ DELETE FROM @{T} WHERE @{I} = $1::@{Y} AND @{O} <> ALL($2::text[])',
        macros,
      ),
      [tableIds(m, [id])[0], org ?? scope.keep?.get(id) ?? []],
    );
  }
}

/**
 * For delete events on a folded key: the organisations of the documents still in MongoDB (and copied: not skipped) whose id folds to
 * the same key, per deleted id. Their rows are not the deleted document's. The only folded key (the outbox) keeps the ObjectId's
 * second, so such documents were created in the same second.
 */
export async function liveHolders(
  rt: MigrationRuntime,
  m: CollectionMapping,
  ids: string[],
): Promise<Map<string, string[]>> {
  const out = new Map<string, string[]>();
  const fold = m.tableId;
  if (!fold || !folds(m)) return out;
  for (const id of ids) {
    if (!/^[0-9a-fA-F]{24}$/.test(id)) continue;
    const s = parseInt(id.slice(0, 8), 16);
    const near = await sourceDb(rt, m)
      .collection(m.collection)
      .find(
        {
          _id: {
            $gte: ObjectId.createFromTime(s),
            $lt: ObjectId.createFromTime(s + 1),
          },
        } as never,
        { promoteLongs: false },
      )
      .toArray();
    const orgs = near
      .filter(
        (d) =>
          String(d._id).toLowerCase() !== id.toLowerCase() &&
          fold(String(d._id).toLowerCase()) === fold(id.toLowerCase()) &&
          !m.skip?.(toCanonical(d, new SourceReport(), m.collection)),
      )
      .flatMap((d) => orgOfSource(m, d) ?? []);
    if (orgs.length) out.set(id, orgs);
  }
  return out;
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
  // on a folded key a document's rows are deleted under its own organisation only; one that names none has no row
  const org = new Map<string, string>();
  if (folds(m))
    raw.forEach((d, i) => {
      const o = orgOfSource(m, d);
      if (o) org.set(ids[i] as string, o);
    });
  const own = (list: string[]): string[] =>
    folds(m) ? list.filter((id) => org.has(id)) : list;
  // and not when another document still in MongoDB and copied holds the same key under the same organisation: the row is that one's
  const notHeld = async (list: string[]): Promise<string[]> => {
    const mine = own(list);
    if (!folds(m) || mine.length === 0) return mine;
    const held = await liveHolders(rt, m, mine);
    return mine.filter(
      (id) => !(held.get(id) ?? []).includes(org.get(id) as string),
    );
  };
  let followUps: Array<{ kind: string }> = [];
  const repo = repositoryFor(m);
  const write = async (inPlace: boolean): Promise<void> => {
    await rt.jobs(async (tx: Tx, k) => {
      if (inPlace) await deleteChildren(k, m, ids);
      else await deleteDocs(k, m, await notHeld(ids), { org });
      const stats = await withUpsert(inPlace ? [m.table] : [], () =>
        repo.importBatch(m.collection, docs, tx),
      );
      followUps = stats.followUps;
      // a document the copy skips now (an outbox event published since it was copied) must not keep the row of its earlier state;
      // a skipped part of a document (a project member) is reported under another id and is not a document to remove
      if (inPlace)
        await deleteDocs(
          k,
          m,
          await notHeld(
            stats.skipped.map((s) => s.id).filter((id) => ids.includes(id)),
          ),
          { org },
        );
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
