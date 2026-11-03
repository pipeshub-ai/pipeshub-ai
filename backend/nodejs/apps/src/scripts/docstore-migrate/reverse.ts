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
import { storeEpochOnFreshConnection } from './online';
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
    // Written back in full: MongoDB still holds the pre-move status, so skipping would turn a sign-in revoked after the move live again.
    if (m.collection === 'oauthAgentRegistrations') {
      const model = modelFor(m.collection);
      if (!model) throw new Error(`${m.collection}: no Mongoose model`);
      reversed[m.collection] = await reverseCollection(rt, m, model, kept);
      log('reversed', { collection: m.collection, ...reversed[m.collection] });
      continue;
    }
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
const hookOn = (failAt: string | undefined, name: string): boolean =>
  failAt === name && process.env.MIGRATE_TEST_HOOKS === '1';
const errText = (e: unknown): string =>
  e instanceof Error ? e.message : String(e);

/**
 * What a freeze did. `unknown`: the commit failed and the row could not be read back, so PostgreSQL may or may not be read-only; nothing
 * may be written back to `readonly` until an operator has read it.
 */
export type Freeze =
  | { outcome: 'applied'; previous: boolean }
  | { outcome: 'not_applied'; why: string }
  | { outcome: 'unknown'; why: string };

/** PostgreSQL read-only: the barrier lock (waits for every writer in flight, later ones are refused) and `readonly` in one transaction. */
export async function freezePostgres(
  rt: MigrationRuntime,
  failAt?: string,
): Promise<Freeze> {
  let before: boolean;
  try {
    if (hookOn(failAt, 'freeze'))
      throw new Error('injected failure of the freeze');
    before =
      (
        await rt.migratePool.query<{ readonly: boolean }>(
          'SELECT readonly FROM platform.store_epoch',
        )
      ).rows[0]?.readonly ?? false;
  } catch (e) {
    return { outcome: 'not_applied', why: errText(e) };
  }
  const c = await rt.migratePool.connect();
  try {
    await c.query('BEGIN');
    await c.query("SET LOCAL lock_timeout = '20s'");
    await c.query('SELECT pg_advisory_xact_lock(7340, 1)');
    await c.query(
      'UPDATE platform.store_epoch SET readonly = true, updated_at = now()',
    );
    await c.query('COMMIT');
    if (hookOn(failAt, 'freeze-commit') || hookOn(failAt, 'freeze-unknown'))
      throw new Error('injected: the freeze committed, then an error');
    return { outcome: 'applied', previous: before };
  } catch (e) {
    await c.query('ROLLBACK').catch(() => undefined);
    // the commit may have reached the server before the error reached us: the row is read on a connection of its own
    const now = await storeEpochOnFreshConnection(
      rt,
      hookOn(failAt, 'freeze-unknown'),
    ).catch(() => null);
    if (now === null) return { outcome: 'unknown', why: errText(e) };
    // read-only before: the freeze changes nothing, so the value to restore is the same either way
    return now.readonly
      ? { outcome: 'applied', previous: before }
      : { outcome: 'not_applied', why: errText(e) };
  } finally {
    c.release();
  }
}

/** The report text for a freeze that may have happened: nothing is reopened and the operator reads the row first. */
export const freezeUnknown = (why: string): string =>
  `whether PostgreSQL was frozen is unknown (${why}): platform.store_epoch.readonly could not be read back. Read it before any manual write; nothing else was changed and nothing was reopened`;

/** Puts `readonly` back to what the freeze found, only while PostgreSQL is still the store. False: no row was updated. */
export async function unfreezePostgres(
  rt: MigrationRuntime,
  previous: boolean,
): Promise<boolean> {
  const up = await rt.migratePool.query(
    "UPDATE platform.store_epoch SET readonly = $1, updated_at = now() WHERE backend = 'postgres'",
    [previous],
  );
  return up.rowCount === 1;
}

/** unfreezePostgres for the failure paths: never throws, returns what to add to the message when PostgreSQL could not be reopened. */
export async function releasePostgres(
  rt: MigrationRuntime,
  previous: boolean,
  failAt?: string,
): Promise<string> {
  try {
    if (hookOn(failAt, 'unfreeze'))
      throw new Error('injected failure of the unfreeze');
    if (await unfreezePostgres(rt, previous)) return '';
  } catch {
    // reported below
  }
  return ` PostgreSQL is still read-only: run \`UPDATE platform.store_epoch SET readonly = ${String(previous)} WHERE backend = 'postgres'\` to reopen it.`;
}

/** Nothing to roll back unless PostgreSQL is the store: a second run must not reopen a closed PostgreSQL. */
export async function notPostgresStore(
  rt: MigrationRuntime,
): Promise<string | undefined> {
  const b = (
    await rt.migratePool.query<{ backend: string }>(
      'SELECT backend FROM platform.store_epoch',
    )
  ).rows[0]?.backend;
  return b === 'postgres'
    ? undefined
    : `the store is '${String(b)}', not PostgreSQL: there is nothing to roll back`;
}

/** One rollback at a time (a session lock on its own connection): two would hand the stores over twice or reopen what the other froze. */
export async function whileExclusive<T>(
  rt: MigrationRuntime,
  busy: () => T,
  run: () => Promise<T>,
): Promise<T> {
  const c = await rt.migratePool.connect();
  try {
    const got = (
      await c.query<{ ok: boolean }>(
        'SELECT pg_try_advisory_lock(7340, 2) AS ok',
      )
    ).rows[0]?.ok;
    if (!got) return busy();
    try {
      return await run();
    } finally {
      await c
        .query('SELECT pg_advisory_unlock(7340, 2)')
        .catch(() => undefined);
    }
  } finally {
    c.release();
  }
}
export const BUSY =
  'another rollback is running against this store: wait for it to finish';

export type HandOver =
  | { kind: 'done' }
  | { kind: 'not_live'; why: string }
  | { kind: 'stuck'; message: string };

/**
 * MongoDB live first, then the store row, each read back when its answer is lost. `not_live`: MongoDB is confirmed still retired, so the
 * caller may reopen PostgreSQL. `stuck`: MongoDB is (or may be) live; PostgreSQL stays read-only and the message says where things stand.
 */
export async function handOver(
  rt: MigrationRuntime,
  epoch: number,
  failAt?: string,
): Promise<HandOver> {
  try {
    if (hookOn(failAt, 'fence-not-applied'))
      throw new Error('injected: the fence was not applied');
    await setMongoFence(rt, 'active', epoch);
    if (hookOn(failAt, 'fence-applied') || hookOn(failAt, 'fence-unknown'))
      throw new Error('injected: the fence was applied, then an error');
  } catch (e) {
    const why = errText(e);
    const now = await (async () => {
      if (hookOn(failAt, 'fence-unknown')) return null;
      return readMongoFence(rt).catch(() => null);
    })();
    if (now?.mode === 'retired' && now.epoch === epoch - 1)
      return { kind: 'not_live', why };
    const seen = now
      ? `the fence reads '${now.mode}' at epoch ${String(now.epoch)}`
      : 'the fence could not be read back';
    return {
      kind: 'stuck',
      message: `MongoDB's fence may be live (${why}; ${seen}). PostgreSQL stays read-only and was not unfrozen. If the fence is active at epoch ${String(epoch)}: set platform.store_epoch to backend mongo, readonly true and the epoch one higher by hand, then roll the application back to DOC_STORE=mongo. If it is retired: run \`UPDATE platform.store_epoch SET readonly = false WHERE backend = 'postgres'\` to keep PostgreSQL as the store`,
    };
  }
  try {
    if (hookOn(failAt, 'finalize-epoch'))
      throw new Error('injected failure before the store row');
    const up = await rt.migratePool.query(
      "UPDATE platform.store_epoch SET backend = 'mongo', epoch = epoch + 1, readonly = true, updated_at = now()",
    );
    if (up.rowCount !== 1)
      throw new Error(
        `platform.store_epoch has ${String(up.rowCount)} rows, not one`,
      );
  } catch (e) {
    // it may have committed before the error reached us
    const row = await storeEpochOnFreshConnection(rt, false).catch(() => null);
    if (row?.backend === 'mongo') return { kind: 'done' };
    return {
      kind: 'stuck',
      message: `MongoDB is the live store again (its fence is active), but platform.store_epoch was not updated (${errText(e)}). PostgreSQL is still read-only and was not unfrozen. Set platform.store_epoch to backend mongo, readonly true and the epoch one higher by hand, then roll the application back to DOC_STORE=mongo`,
    };
  }
  return { kind: 'done' };
}

export async function rollback(
  rt: MigrationRuntime,
  o: { reverse: boolean; allowWriters: boolean; log: Log; failAt?: string },
): Promise<RollbackReport> {
  return whileExclusive<RollbackReport>(
    rt,
    () => ({ ok: false, mode: 'refused', message: BUSY }),
    () => rollbackLocked(rt, o),
  );
}

async function rollbackLocked(
  rt: MigrationRuntime,
  o: { reverse: boolean; allowWriters: boolean; log: Log; failAt?: string },
): Promise<RollbackReport> {
  const notStore = await notPostgresStore(rt);
  if (notStore) return { ok: false, mode: 'refused', message: notStore };
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
  const frozen = await freezePostgres(rt, o.failAt);
  if (frozen.outcome === 'not_applied')
    return {
      ok: false,
      mode: 'refused',
      message: `PostgreSQL could not be frozen: ${frozen.why}. Nothing was changed`,
    };
  if (frozen.outcome === 'unknown')
    return { ok: false, mode: 'refused', message: freezeUnknown(frozen.why) };
  const { previous } = frozen;
  const stuck = (message: string): RollbackReport => ({
    ok: false,
    mode: 'reverse',
    message,
  });
  // the hand-over result decides what a failure may do: once MongoDB may be live PostgreSQL stays read-only
  const state = { released: false };
  const release = (): Promise<string> => {
    state.released = true;
    return releasePostgres(rt, previous, o.failAt);
  };
  const finish = async (): Promise<HandOver> =>
    handOver(rt, fence.epoch + 1, o.failAt);
  try {
    const v = await verifyAll(rt, { log: o.log });
    if (v.ok) {
      const h = await finish();
      if (h.kind === 'stuck') return stuck(h.message);
      if (h.kind === 'not_live') {
        const note = await release();
        throw new Error(`${h.why}.${note}`);
      }
      const cur = await JobState.latest(rt);
      return {
        ok: true,
        mode: 'no_pg_writes',
        verify: v,
        message: `PostgreSQL equals MongoDB (job ${String(cur?.id ?? '-')}): nothing was written there. MongoDB is the live store again: set DOC_STORE=mongo and start the application`,
      };
    }
    if (!o.reverse) {
      const note = await release();
      return {
        ok: false,
        mode: 'refused',
        verify: v,
        message:
          'PostgreSQL differs from MongoDB (the application has written to it, or the copy is wrong): MongoDB is NOT made live. Run `rollback --reverse` to copy PostgreSQL back first.' +
          note,
      };
    }
    const r = await reverseCopy(rt, o.log);
    const after = await verifyAll(rt, { log: o.log, afterReverse: true });
    const manual = r.notReversed.length;
    if (!after.ok || manual > 0) {
      const note = await release();
      return {
        ok: false,
        mode: 'reverse',
        verify: after,
        ...r,
        message:
          (manual
            ? 'some collections need their own reverse step (see notReversed); MongoDB is not made live'
            : 'the reverse copy does not verify; MongoDB is not made live') +
          note,
      };
    }
    const h = await finish();
    if (h.kind === 'stuck') return stuck(h.message);
    if (h.kind === 'not_live') {
      const note = await release();
      throw new Error(`${h.why}.${note}`);
    }
    return {
      ok: true,
      mode: 'reverse',
      verify: after,
      ...r,
      message:
        'PostgreSQL was copied back and verified: set DOC_STORE=mongo and start the application',
    };
  } catch (e) {
    if (!state.released) {
      const note = await release();
      if (note && e instanceof Error) e.message += `.${note}`; // the original error stays, with what is left to do
    }
    throw e;
  }
}
