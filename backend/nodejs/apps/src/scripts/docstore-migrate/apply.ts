/** Replacing and deleting whole documents in PostgreSQL (follow mode): the mapping says which tables hold a document. */
import type { Tx as KernelTx } from '../../libs/db';
import { ObjectId } from 'mongodb';
import {
  withUpsert,
  type NeutralDoc,
  type Row,
} from '../../libs/repositories/import';
import {
  outboxEventOf,
  rowIsEvent,
} from '../../libs/repositories/outbox/pg-import';
import { Logger } from '../../libs/services/logger.service';
import { fill } from '../../libs/repositories/pg-util';
import type { Tx } from '../../libs/repositories/uow';
import { runFollowUps } from './follow-ups';
import type { CollectionMapping } from './mapping/types';
import { repositoryFor, sourceDb } from './bulk';
import type { MigrationRuntime } from './runtime';
import { SourceReport, toCanonical, toNeutral } from './source';
import { dropKeys, keysOf } from './carry/keymap';
import { deleteKeyedRow, recordKeys } from './keyed-rows';
import { putRefusals } from './refusals';
import { writeWithDocuments } from './carry/run';

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
 * The outbox folds an event's ObjectId into a table id that two events can share (same second, same low counter bits), in one
 * organisation or in two. Which event a row holds is decided only by `rowIsEvent` (the event's unchanging fields), never by the id
 * or the organisation alone.
 */
const folds = (m: CollectionMapping): boolean => Boolean(m.tableId);

/** The identity of the row a document is stored as, for a folded table; null when it can have none. */
const eventOf = (m: CollectionMapping, d: NeutralDoc): Row | null => {
  if (m.collection !== 'outbox_events')
    throw new Error(
      `${m.collection}: a folded table key needs an event identity`,
    );
  return outboxEventOf(d);
};

let undecidedLogged = false;
/** A row whose event cannot be told is kept; said once per process, so an operator can look. */
const undecided = (m: CollectionMapping, row: Row): void => {
  if (undecidedLogged) return;
  undecidedLogged = true;
  Logger.getInstance({ service: 'DocstoreFollow' }).warn(
    'a row of a folded table key lacks the fields that say which event it holds; it is kept',
    { table: m.table, id: String(row.id) },
  );
};

/** The rows stored under these folded table ids, with the fields that identify their event. */
async function rowsAt(
  k: KernelTx,
  m: CollectionMapping,
  keys: string[],
): Promise<Row[]> {
  if (keys.length === 0) return [];
  const { rows } = await k.query<Row>(
    fill(
      '/* pipeshub:follow.folded_rows */ SELECT t.@{O} AS org_id, t.@{I}::text AS id, t.topic, t.msg_key, t.ordering_key, t.value, t.created_at FROM @{T} t WHERE t.@{I} = ANY($1::@{Y}[])',
      {
        T: ident(m.table),
        I: ident(m.idCol),
        O: ident(m.orgCol as string),
        Y: m.idType ?? 'text',
      },
    ),
    [[...new Set(keys)]],
  );
  return rows;
}

/** Deletes exactly these rows, by organisation and id (the table's key). */
async function deleteRows(
  k: KernelTx,
  m: CollectionMapping,
  rows: Row[],
): Promise<void> {
  if (rows.length === 0) return;
  await k.query(
    fill(
      '/* pipeshub:follow.delete_folded */ DELETE FROM @{T} t USING unnest($1::text[], $2::@{Y}[]) AS w(org_id, id) WHERE t.@{O} = w.org_id AND t.@{I} = w.id',
      {
        T: ident(m.table),
        I: ident(m.idCol),
        O: ident(m.orgCol as string),
        Y: m.idType ?? 'text',
      },
    ),
    [rows.map((r) => r.org_id), rows.map((r) => r.id)],
  );
}

/** Folded table: deletes the rows that are these events (documents whose content is known), and no other row under their ids. */
async function deleteEventRows(
  k: KernelTx,
  m: CollectionMapping,
  events: Row[],
): Promise<void> {
  if (events.length === 0) return;
  const gone = (
    await rowsAt(
      k,
      m,
      events.map((e) => String(e.id)),
    )
  ).filter((r) =>
    events.some((e) => {
      if (String(e.id) !== String(r.id)) return false;
      const is = rowIsEvent(r, e);
      if (is === undefined) undecided(m, r);
      return is === true;
    }),
  );
  await deleteRows(k, m, gone);
}

/**
 * Folded table, delete events (they carry only the id): the rows under those ids that are none of the documents still in MongoDB
 * and copied there (`live`) belong to deleted documents and go; a row that is one of them stays, and so does a row that cannot be
 * told apart.
 */
export async function deleteUnclaimedRows(
  k: KernelTx,
  m: CollectionMapping,
  ids: string[],
  live: Row[],
): Promise<void> {
  const gone = (await rowsAt(k, m, tableIds(m, ids))).filter((r) => {
    const verdicts = live
      .filter((l) => String(l.id) === String(r.id))
      .map((l) => rowIsEvent(r, l));
    if (verdicts.includes(true)) return false;
    if (rowIsEvent(r, r) === undefined) {
      undecided(m, r);
      return false;
    }
    return true;
  });
  await deleteRows(k, m, gone);
}

/** Children first, then the root row. A folded table is never deleted from by id alone: see `deleteUnclaimedRows`. */
export async function deleteDocs(
  k: KernelTx,
  m: CollectionMapping,
  ids: string[],
): Promise<void> {
  if (ids.length === 0) return;
  if (m.keyed) {
    // the event names the document id, which the table does not keep: the key map names its row
    const lower = ids.map((i) => i.toLowerCase());
    for (const e of await keysOf(k, m.collection, lower))
      await deleteKeyedRow(k, m, e);
    await dropKeys(k, m.collection, lower);
    return;
  }
  if (folds(m))
    throw new Error(
      `${m.table}: the table id is folded; delete by the event (deleteUnclaimedRows), not by id`,
    );
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
 * For delete events on a folded table: the identities of the documents still in MongoDB, and copied (not skipped), whose id folds
 * to one of these ids. The outbox id keeps the ObjectId's second, so such documents were created in the same second.
 */
export async function liveEvents(
  rt: MigrationRuntime,
  m: CollectionMapping,
  ids: string[],
): Promise<Row[]> {
  const fold = m.tableId;
  if (!fold) return [];
  const keys = new Set(ids.map((i) => fold(i.toLowerCase())));
  const seconds = [
    ...new Set(
      ids
        .filter((i) => /^[0-9a-fA-F]{24}$/.test(i))
        .map((i) => parseInt(i.slice(0, 8), 16)),
    ),
  ];
  const out: Row[] = [];
  for (const s of seconds) {
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
    for (const d of near) {
      if (!keys.has(fold(String(d._id).toLowerCase()))) continue;
      if (m.skip?.(toCanonical(d, new SourceReport(), m.collection))) continue;
      const e = eventOf(m, toNeutral(d, new SourceReport(), m.collection));
      if (e) out.push(e);
    }
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
  const events = (list: string[]): Row[] =>
    list.flatMap((id): Row[] => {
      const e = eventOf(m, docs[ids.indexOf(id)] as NeutralDoc);
      return e ? [e] : [];
    });
  let followUps: Array<{ kind: string }> = [];
  const repo = repositoryFor(m);
  const write = async (inPlace: boolean): Promise<void> => {
    await rt.jobs(async (tx: Tx, k) => {
      // a folded table: each document's own row goes first and the copied ones are written again, so a row the copy now skips (an
      // event published since it was copied) is gone before another event of the batch is checked against its folded id
      if (folds(m)) await deleteEventRows(k, m, events(ids));
      else if (inPlace) await deleteChildren(k, m, ids);
      else await deleteDocs(k, m, ids);
      const stats = await withUpsert(inPlace ? [m.table] : [], () =>
        repo.importBatch(m.collection, docs, tx),
      );
      followUps = stats.followUps;
      // a document the copy skips now must not keep the row of its earlier state; a skipped part of a document (a project member)
      // is reported under another id and is not a document to remove
      const skipped = stats.skipped
        .map((s) => s.id)
        .filter((id) => ids.includes(id));
      if (inPlace && skipped.length && !folds(m))
        await deleteDocs(k, m, skipped);
      const refused = new Set(stats.skipped.map((x) => x.id));
      await writeWithDocuments(
        k,
        m.collection,
        raw as Array<Record<string, unknown>>,
        new Set([...refused].map((id) => id.toLowerCase())),
      );
      await recordKeys(k, m, docs, refused);
      // a document refused now joins the refusals, and one the importer takes now leaves them
      await putRefusals(
        k,
        m.collection,
        stats.skipped,
        ids.filter((id) => !refused.has(id)),
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
