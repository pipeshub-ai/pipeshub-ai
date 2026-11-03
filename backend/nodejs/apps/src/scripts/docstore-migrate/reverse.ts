/**
 * Rollback (migration design section 8).
 *
 * Before the point of no return (no application write has reached PostgreSQL) MongoDB still holds everything: `rollback`
 * checks that by verifying PostgreSQL against MongoDB, then makes MongoDB the live store again.
 *
 * After it, `rollback --reverse` copies PostgreSQL back: every mapped document is rebuilt from its rows by the verifier's
 * decoder, given its MongoDB types by the application's own schema, and written over the MongoDB document; MongoDB
 * documents that no longer exist in PostgreSQL are removed. The application must be stopped. This is the compose path
 * (a full copy); the online change-log follower for Helm installs is not built in this round.
 *
 * Accepted differences (listed in the report): U+0000 stays stripped, JSON key order, per-organisation slug numbers, authorization
 * codes come back as their hashes (a code in flight at the rollback fails and the client retries).
 */
import mongoose from 'mongoose';
import { Log } from './bulk';
import { MAPPINGS } from './mapping';
import type { CollectionMapping } from './mapping/types';
import { MONGO_COLLECTIONS } from './mongo-collections';
import { idFilter } from './mongo-read';
import { readDecoded, readDecodedPage } from './pg-read';
import type { MigrationRuntime } from './runtime';
import { sourceDb } from './bulk';
import { SPECIAL, reverseSpecial } from './reverse-special';
import {
  appClients,
  describeClients,
  readMongoFence,
  setMongoFence,
} from './fence';
import { JobState } from './state';
import { VerifyReport, verifyAll } from './verify';

// eslint-disable-next-line @typescript-eslint/no-unused-expressions
MONGO_COLLECTIONS; // loading it registers every model

export interface RollbackReport {
  ok: boolean;
  mode: 'no_pg_writes' | 'reverse' | 'refused';
  message: string;
  verify?: VerifyReport;
  reversed?: Record<string, { written: number; removed: number }>;
  notReversed?: string[];
}

export const modelFor = (
  collection: string,
): mongoose.Model<unknown> | undefined =>
  Object.values(mongoose.models).find(
    (m) => m.collection.name === collection,
  ) as mongoose.Model<unknown> | undefined;

/** Collections whose rows cannot be turned back into documents by the generic path. */
export const REVERSE_MANUAL = (m: CollectionMapping): string | null =>
  SPECIAL.has(m.collection)
    ? null
    : m.custom
      ? 'needs its own reverse step'
      : m.sourceKey
        ? 'documents are keyed by something other than _id: needs its own reverse step'
        : null;

/** The MongoDB write for one decoded document: cast by the application's own schema, stored nulls and empty objects kept. */
export function replaceOp(
  model: mongoose.Model<unknown>,
  d: Record<string, unknown>,
): { replaceOne: { filter: object; replacement: object; upsert: true } } {
  const { _id, ...rest } = d;
  const cast = (
    model as unknown as {
      castObject(o: unknown, opts: unknown): Record<string, unknown>;
    }
  ).castObject(rest, { ignoreCastErrors: true });
  // casting drops stored nulls and empty objects; the document had them
  for (const [k, v] of Object.entries(rest))
    if (
      cast[k] === undefined &&
      (v === null || (typeof v === 'object' && !Array.isArray(v)))
    )
      cast[k] = v;
  return {
    replaceOne: {
      filter: { _id: idFilter(String(_id)) },
      replacement: cast,
      upsert: true,
    },
  };
}

/**
 * Brings these documents of a collection level in MongoDB with PostgreSQL: rebuilt from their rows and written over, or removed when
 * PostgreSQL no longer has them. The change-log follower's unit of work.
 */
export async function reverseIds(
  rt: MigrationRuntime,
  m: CollectionMapping,
  ids: string[],
): Promise<{ written: number; removed: number }> {
  const model = modelFor(m.collection);
  if (!model) throw new Error(`${m.collection}: no Mongoose model`);
  const col = sourceDb(rt, m).collection(m.collection);
  const docs = await rt.jobs((_tx, k) => readDecoded(k, m, ids), {
    write: false,
  });
  if (docs.length)
    await col.bulkWrite(
      docs.map((d) => replaceOp(model, d)),
      {
        ordered: false,
      },
    );
  const have = new Set(docs.map((d) => String(d._id).toLowerCase()));
  const gone = ids.filter((id) => !have.has(id.toLowerCase()));
  const r = gone.length
    ? await col.deleteMany({ _id: { $in: gone.map(idFilter) } } as never)
    : { deletedCount: 0 };
  return { written: docs.length, removed: r.deletedCount };
}

/** PostgreSQL keeps slug numbers per organisation, MongoDB's indexes want them global: relax those indexes before documents come back. */
export async function relaxSlugIndexes(
  rt: MigrationRuntime,
  log: Log,
): Promise<void> {
  for (const name of ['users', 'userGroups', 'oauthApps']) {
    const col = rt.db().collection(name);
    for (const ix of await col.indexes().catch(() => [])) {
      const keys = Object.keys(ix.key);
      if (ix.unique && keys.length === 1 && keys[0] === 'slug' && ix.name) {
        await col.dropIndex(ix.name);
        await col.createIndex({ slug: 1 }, { name: ix.name });
        log('slug_index_relaxed', { collection: name, index: ix.name });
      }
    }
  }
}

/** Writes every document of a collection back from its rows and removes the MongoDB documents PostgreSQL no longer has (except `kept`). */
export async function reverseCollection(
  rt: MigrationRuntime,
  m: CollectionMapping,
  model: mongoose.Model<unknown>,
  kept: unknown[],
): Promise<{ written: number; removed: number }> {
  const col = sourceDb(rt, m).collection(m.collection);
  let after: string | null = null;
  let written = 0;
  let removed = 0;
  for (;;) {
    const docs = await rt.jobs(
      (_tx, k) => readDecodedPage(k, m, after, rt.cfg.batchSize),
      { write: false },
    );
    if (docs.length === 0) break;
    const last = String(docs[docs.length - 1]?._id);
    const ops = docs.map((d) => replaceOp(model, d));
    const r = await col.bulkWrite(ops, { ordered: false });
    written +=
      r.upsertedCount + r.modifiedCount + r.matchedCount - r.modifiedCount;
    const ids = docs.map((d) => idFilter(String(d._id)));
    const gone = await col.deleteMany({
      _id: {
        ...(after ? { $gt: idFilter(after) } : {}),
        $lte: idFilter(last),
        $nin: [...ids, ...kept],
      },
      ...(m.keepOnReverse ? { $nor: [m.keepOnReverse] } : {}),
    } as never);
    removed += gone.deletedCount;
    after = last;
  }
  const tail = await col.deleteMany({
    _id: { ...(after ? { $gt: idFilter(after) } : {}), $nin: kept },
    ...(m.keepOnReverse ? { $nor: [m.keepOnReverse] } : {}),
  } as never);
  removed += tail.deletedCount;
  return { written, removed };
}

export async function reverseCopy(
  rt: MigrationRuntime,
  log: Log,
): Promise<Required<Pick<RollbackReport, 'reversed' | 'notReversed'>>> {
  const reversed: Record<string, { written: number; removed: number }> = {};
  const notReversed: string[] = [];
  await relaxSlugIndexes(rt, log);
  // documents the copy left out and listed are still in MongoDB and must stay there
  const job = await JobState.latest(rt);
  for (const m of MAPPINGS) {
    const kept = Object.keys(
      job?.progress.collections[m.collection]?.skippedIds ?? {},
    ).map((id) => idFilter(id));
    // pending device sign-ins live minutes and MongoDB still holds the originals: nothing to bring back (a client starts again)
    if (m.collection === 'oauthDeviceCodes') continue;
    const manual = REVERSE_MANUAL(m);
    if (manual) {
      notReversed.push(`${m.collection}: ${manual}`);
      continue;
    }
    if (SPECIAL.has(m.collection)) {
      reversed[m.collection] = await reverseSpecial(rt, m.collection);
      log('reversed', { collection: m.collection, ...reversed[m.collection] });
      continue;
    }
    if (m.custom) {
      notReversed.push(`${m.collection}: ${REVERSE_MANUAL(m) ?? 'custom'}`);
      continue;
    }
    const model = modelFor(m.collection);
    if (!model) {
      notReversed.push(`${m.collection}: no Mongoose model`);
      continue;
    }
    reversed[m.collection] = await reverseCollection(rt, m, model, kept);
    log('reversed', { collection: m.collection, ...reversed[m.collection] });
  }
  return { reversed, notReversed };
}
/** PostgreSQL read-only: the barrier lock (waits for every writer in flight, later ones are refused) and `readonly` in one transaction. */
export async function freezePostgres(rt: MigrationRuntime): Promise<void> {
  const c = await rt.migratePool.connect();
  try {
    await c.query('BEGIN');
    await c.query("SET LOCAL lock_timeout = '20s'");
    await c.query('SELECT pg_advisory_xact_lock(7340, 1)');
    await c.query(
      'UPDATE platform.store_epoch SET readonly = true, updated_at = now()',
    );
    await c.query('COMMIT');
  } catch (e) {
    await c.query('ROLLBACK').catch(() => undefined);
    throw e;
  } finally {
    c.release();
  }
}

export const unfreezePostgres = async (rt: MigrationRuntime): Promise<void> => {
  await rt.migratePool
    .query(
      'UPDATE platform.store_epoch SET readonly = false, updated_at = now()',
    )
    .catch(() => undefined);
};

export async function rollback(
  rt: MigrationRuntime,
  o: { reverse: boolean; allowWriters: boolean; log: Log },
): Promise<RollbackReport> {
  if (!o.allowWriters) {
    const clients = await appClients(rt);
    if (clients.length > 0)
      return {
        ok: false,
        mode: 'refused',
        message: `the application is still connected: ${describeClients(clients)}. Stop it before a rollback`,
      };
  }
  const fence = await readMongoFence(rt);
  // an application on PostgreSQL could still write while the copy is compared and copied back, and those writes would be lost: PostgreSQL
  // is read-only from here until MongoDB is made the store (or the rollback ends without it)
  try {
    await freezePostgres(rt);
  } catch (e) {
    return {
      ok: false,
      mode: 'refused',
      message: `PostgreSQL could not be frozen: ${e instanceof Error ? e.message : String(e)}. Nothing was changed`,
    };
  }
  const state = { handed: false };
  // MongoDB live first, then the store row: a failure between them leaves MongoDB writable and PostgreSQL read-only, never both writable
  const handOver = async (): Promise<void> => {
    await setMongoFence(rt, 'active', fence.epoch + 1);
    state.handed = true;
    const up = await rt.migratePool.query(
      "UPDATE platform.store_epoch SET backend = 'mongo', epoch = epoch + 1, readonly = true, updated_at = now()",
    );
    if (up.rowCount !== 1)
      throw new Error(
        `platform.store_epoch has ${String(up.rowCount)} rows, not one`,
      );
  };
  try {
    const v = await verifyAll(rt, { log: o.log });
    if (v.ok) {
      await handOver();
      const cur = await JobState.latest(rt);
      return {
        ok: true,
        mode: 'no_pg_writes',
        verify: v,
        message: `PostgreSQL equals MongoDB (job ${String(cur?.id ?? '-')}): nothing was written there. MongoDB is the live store again: set DOC_STORE=mongo and start the application`,
      };
    }
    if (!o.reverse) {
      await unfreezePostgres(rt);
      return {
        ok: false,
        mode: 'refused',
        verify: v,
        message:
          'PostgreSQL differs from MongoDB (the application has written to it, or the copy is wrong): MongoDB is NOT made live. Run `rollback --reverse` to copy PostgreSQL back first',
      };
    }
    const r = await reverseCopy(rt, o.log);
    const after = await verifyAll(rt, { log: o.log, afterReverse: true });
    const manual = r.notReversed.length;
    if (!after.ok || manual > 0) {
      await unfreezePostgres(rt);
      return {
        ok: false,
        mode: 'reverse',
        verify: after,
        ...r,
        message: manual
          ? 'some collections need their own reverse step (see notReversed); MongoDB is not made live'
          : 'the reverse copy does not verify; MongoDB is not made live',
      };
    }
    await handOver();
    return {
      ok: true,
      mode: 'reverse',
      verify: after,
      ...r,
      message:
        'PostgreSQL was copied back and verified: set DOC_STORE=mongo and start the application',
    };
  } catch (e) {
    const why = e instanceof Error ? e.message : String(e);
    if (state.handed)
      // MongoDB is the store; the row that says so could not be written. PostgreSQL stays read-only and is not unfrozen.
      return {
        ok: false,
        mode: 'reverse',
        message: `MongoDB is the live store again, but platform.store_epoch was not updated (${why}). PostgreSQL is still read-only. Set platform.store_epoch to backend mongo, readonly true and the epoch one higher by hand, then set DOC_STORE=mongo and start the application`,
      };
    await unfreezePostgres(rt);
    throw e;
  }
}
