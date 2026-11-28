/**
 * The carriers' steps in a move (R45-CARRY): the copy after the generic one, the follower's events, the verify, the resync after
 * a lost resume token, the rollback and the reset. Every step runs a carrier's store group as one unit; the MongoDB side goes
 * through `CarryMongo`, so the same steps run against a fake in the unit tests.
 */
import { ObjectId, type Db } from 'mongodb';
import type { Tx as KernelTx } from '../../../libs/db';
import { CARRIERS, carriedSources } from './index';
import { clearKeys } from './keymap';
import { canonicalText, sourceDoc } from './plain';
import type {
  Carrier,
  CarryMongo,
  Doc,
  Restored,
  SourceDoc,
  SourceRead,
} from './types';

/** How a migration step reaches PostgreSQL: a unit of the import login. */
export type CarryPg = <T>(
  fn: (k: KernelTx) => Promise<T>,
  write: boolean,
) => Promise<T>;

const BATCH = 500;

const readOf = (c: Carrier, collection: string): SourceRead =>
  c.reads?.[collection] ?? {};

const mongoId = (id: string, oid: boolean): ObjectId | string =>
  oid ? new ObjectId(id) : id;

/** `CarryMongo` on a MongoDB database. */
export function mongoCarry(db: Db): CarryMongo {
  return {
    async *scan(collection, read = {}) {
      const projection = read.pick
        ? Object.fromEntries(read.pick.map((p) => [p, 1]))
        : undefined;
      const cursor = db
        .collection(collection)
        .find({}, projection ? { projection } : {});
      if (read.order === 'id') cursor.sort({ _id: 1 });
      for await (const raw of cursor) yield sourceDoc(raw, collection, read);
    },
    async replace(collection, docs) {
      if (!docs.length) return;
      await db.collection(collection).bulkWrite(
        docs.map((d) => ({
          replaceOne: {
            filter: { _id: mongoId(d.id, d.oid) } as never,
            replacement: d.doc,
            upsert: true,
          },
        })),
        { ordered: false },
      );
    },
    async update(collection, ops) {
      if (!ops.length) return;
      await db.collection(collection).bulkWrite(
        ops.map((o) => ({
          updateOne: {
            filter: { _id: mongoId(o.id, o.oid) } as never,
            update: {
              ...(Object.keys(o.set).length ? { $set: o.set } : {}),
              ...(o.unset.length
                ? { $unset: Object.fromEntries(o.unset.map((p) => [p, ''])) }
                : {}),
            },
          },
        })),
        { ordered: false },
      );
    },
    async remove(collection, ids) {
      if (!ids.length) return 0;
      const r = await db.collection(collection).deleteMany({
        _id: { $in: ids.map((i) => mongoId(i.id, i.oid)) },
      } as never);
      return r.deletedCount;
    },
  };
}

/** Carriers that read any of the collections named (all of them without a list). */
export const carriersFor = (only?: readonly string[]): Carrier[] =>
  only
    ? CARRIERS.filter((c) => carriedSources(c).some((s) => only.includes(s)))
    : [...CARRIERS];

/** Copies every carrier's documents (insert or replace: a repeated copy converges). Returns documents read per carrier and collection. */
export async function copyCarried(
  pg: CarryPg,
  mongo: CarryMongo,
  carriers: readonly Carrier[],
  log: (event: string, data?: Record<string, unknown>) => void = () =>
    undefined,
): Promise<Record<string, number>> {
  const out: Record<string, number> = {};
  for (const c of carriers)
    for (const coll of carriedSources(c)) {
      let n = 0;
      let batch: SourceDoc[] = [];
      const flush = async (): Promise<void> => {
        const docs = batch;
        batch = [];
        if (docs.length) await pg((k) => c.write(k, coll, docs), true);
      };
      for await (const d of mongo.scan(coll, readOf(c, coll))) {
        if (d.refused)
          throw new Error(
            `${d.refused}: nothing more was copied; correct the document, then copy again`,
          );
        batch.push(d);
        n += 1;
        if (batch.length >= BATCH) await flush();
      }
      await flush();
      out[`${c.name}:${coll}`] = n;
      log('carried', { carrier: c.name, collection: coll, documents: n });
    }
  return out;
}

/** The follower's events for one collection: the documents written (raw, as the change stream gave them) and the ids removed. */
export async function applyCarried(
  pg: CarryPg,
  collection: string,
  upserts: Doc[],
  deletes: string[],
  carriers: readonly Carrier[] = CARRIERS,
): Promise<void> {
  if (!upserts.length && !deletes.length) return;
  for (const c of carriers) {
    if (!carriedSources(c).includes(collection)) continue;
    const docs = upserts.map((raw) =>
      sourceDoc(raw, collection, readOf(c, collection)),
    );
    const bad = docs.find((d) => d.refused);
    if (bad)
      throw new Error(
        `${bad.refused ?? ''}: the follower stopped; correct the document, then run follow again`,
      );
    await pg(async (k) => {
      if (deletes.length) await c.remove(k, collection, deletes);
      if (docs.length) await c.write(k, collection, docs);
    }, true);
  }
}

export interface CarryVerify {
  carrier: string;
  collection: string;
  /** Entries the documents call for, and rows found. */
  expected: number;
  actual: number;
  /** Keys only: a carried value (a wrapped key, a sealed manifest) is never put in a report. */
  missing: string[];
  extra: string[];
  differing: Array<{ key: string; fields: string[] }>;
  /** Two documents that call for the same row. */
  duplicates: string[];
  /** Listed transforms applied, by name. */
  transforms: Record<string, number>;
  /** A document the carrier refuses (the copy would refuse it too). */
  refused: string[];
  ok: boolean;
}

const MAX_LISTED = 50;

/** The top-level fields whose canonical text differs (the names only). */
function differingFields(a: unknown, b: unknown): string[] {
  const ao = a !== null && typeof a === 'object' ? (a as Doc) : { '': a };
  const bo = b !== null && typeof b === 'object' ? (b as Doc) : { '': b };
  const keys = new Set([...Object.keys(ao), ...Object.keys(bo)]);
  return [...keys]
    .filter((k) => canonicalText(ao[k]) !== canonicalText(bo[k]))
    .sort();
}

/** Compares what the documents call for with what PostgreSQL holds, carrier by carrier and collection by collection. */
export async function verifyCarried(
  pg: CarryPg,
  mongo: CarryMongo,
  carriers: readonly Carrier[],
): Promise<CarryVerify[]> {
  const out: CarryVerify[] = [];
  for (const c of carriers)
    for (const coll of carriedSources(c)) {
      const want = new Map<string, unknown>();
      const transforms: Record<string, number> = {};
      const duplicates: string[] = [];
      const refused: string[] = [];
      const note = (t: string): void => {
        transforms[t] = (transforms[t] ?? 0) + 1;
      };
      for await (const d of mongo.scan(coll, readOf(c, coll))) {
        if (d.refused) {
          refused.push(d.refused);
          continue;
        }
        let entries: Array<[string, unknown]>;
        try {
          entries = c.expected(coll, d, note);
        } catch (e) {
          refused.push(e instanceof Error ? e.message : String(e));
          continue;
        }
        for (const [key, value] of entries) {
          if (want.has(key)) duplicates.push(key);
          want.set(key, value);
        }
      }
      const have = await pg((k) => c.actual(k, coll), false);
      const missing: string[] = [];
      const differing: Array<{ key: string; fields: string[] }> = [];
      for (const [key, value] of want) {
        if (!have.has(key)) missing.push(key);
        else if (canonicalText(value) !== canonicalText(have.get(key)))
          differing.push({
            key,
            fields: differingFields(value, have.get(key)),
          });
      }
      const extra = [...have.keys()].filter((k) => !want.has(k));
      out.push({
        carrier: c.name,
        collection: coll,
        expected: want.size,
        actual: have.size,
        missing: missing.slice(0, MAX_LISTED),
        extra: extra.slice(0, MAX_LISTED),
        differing: differing.slice(0, MAX_LISTED),
        duplicates: duplicates.slice(0, MAX_LISTED),
        transforms,
        refused: refused.slice(0, MAX_LISTED),
        ok:
          !missing.length &&
          !extra.length &&
          !differing.length &&
          !duplicates.length &&
          !refused.length,
      });
    }
  for (const c of carriers) {
    if (!c.check) continue;
    const problems = await pg(
      (k) => (c.check as NonNullable<Carrier['check']>)(k, mongo),
      false,
    );
    out.push({
      carrier: c.name,
      collection: '(check)',
      expected: 0,
      actual: 0,
      missing: [],
      extra: [],
      differing: [],
      duplicates: [],
      transforms: {},
      refused: problems.slice(0, MAX_LISTED),
      ok: problems.length === 0,
    });
  }
  return out;
}

/** Rollback: every carried collection written back from PostgreSQL. Run after the generic reverse (which rewrites whole documents). */
export async function restoreCarried(
  pg: CarryPg,
  mongo: CarryMongo,
  carriers: readonly Carrier[],
  log: (event: string, data?: Record<string, unknown>) => void = () =>
    undefined,
): Promise<Record<string, Restored>> {
  const out: Record<string, Restored> = {};
  for (const c of carriers)
    for (const coll of carriedSources(c)) {
      out[`${c.name}:${coll}`] = await pg(
        (k) => c.restore(k, coll, mongo),
        true,
      );
      log('reversed', {
        carrier: c.name,
        collection: coll,
        ...out[`${c.name}:${coll}`],
      });
    }
  return out;
}

/** Removes everything the carriers wrote and the whole key map (reset; a resync clears its carriers before copying again). */
export async function clearCarried(
  k: KernelTx,
  carriers: readonly Carrier[] = CARRIERS,
  keys = true,
): Promise<void> {
  for (const c of [...carriers].reverse()) await c.clear(k);
  if (keys) await clearKeys(k);
}

/** After a lost resume token: the carriers' rows are written again from what MongoDB holds now (rows of removed documents go). */
export async function resyncCarried(
  pg: CarryPg,
  mongo: CarryMongo,
  carriers: readonly Carrier[] = CARRIERS,
): Promise<void> {
  for (const c of carriers) {
    await pg((k) => c.clear(k), true);
    await copyCarried(pg, mongo, [c]);
  }
}
