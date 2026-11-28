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
  APP_WRITES_ID,
  restoreAppWrites,
  setMongoFence,
} from './fence';
import { storeEpochOnFreshConnection } from './online';
import { JobState, releaseHeld } from './state';
import { VerifyReport, verifyAll } from './verify';
import { carriersFor, releaseCarried, restoreCarried } from './carry/run';
import { carryMongo, carryPg } from './carry/steps';
import type { Carrier } from './carry/types';
import { refusedIds } from './refusals';

// eslint-disable-next-line @typescript-eslint/no-unused-expressions
MONGO_COLLECTIONS; // loading it registers every model

export interface RollbackReport {
  ok: boolean;
  mode: 'no_pg_writes' | 'reverse' | 'released' | 'refused';
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
    const refused = await refusedIds(
      rt,
      m.collection,
      job?.progress.collections[m.collection]?.skippedIds,
    );
    const kept = Object.keys(refused).map((id) => idFilter(id));
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
      reversed[m.collection] = await reverseSpecial(rt, m.collection, refused);
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
  // after the documents: the generic path rewrites a user, an org or a chat whole, so the fields a carrier keeps come back last
  Object.assign(
    reversed,
    await restoreCarried(carryPg(rt), carryMongo(rt), carriersFor(), log),
  );
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
  /** `note`: what is left to do about the application's MongoDB write roles ('' when they are back). */
  | { kind: 'done'; note: string }
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
    // MongoDB is confirmed live: the application's users get their write roles back now, as on the normal path. Unconfirmed, the
    // operator is told how, once they have seen the fence active
    const roles =
      now?.mode === 'active' && now.epoch === epoch
        ? await restoreAppWrites(rt)
        : ` Once the fence reads active, also give the application's MongoDB users back the write roles the switch took (the ${APP_WRITES_ID} document of docstore_fence lists them; grantRolesToUser).`;
    return {
      kind: 'stuck',
      message: `MongoDB's fence may be live (${why}; ${seen}). PostgreSQL stays read-only and was not unfrozen. If the fence is active at epoch ${String(epoch)}: set platform.store_epoch to backend mongo, readonly true and the epoch one higher by hand, then roll the application back to DOC_STORE=mongo. If it is retired: run \`UPDATE platform.store_epoch SET readonly = false WHERE backend = 'postgres'\` to keep PostgreSQL as the store.${roles}`,
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
    // MongoDB is live either way, so the application's users get their write roles back
    const note = await restoreAppWrites(rt);
    if (row?.backend === 'mongo') return { kind: 'done', note };
    return {
      kind: 'stuck',
      message: `MongoDB is the live store again (its fence is active), but platform.store_epoch was not updated (${errText(e)}). PostgreSQL is still read-only and was not unfrozen. Set platform.store_epoch to backend mongo, readonly true and the epoch one higher by hand, then roll the application back to DOC_STORE=mongo.${note}`,
    };
  }
  // MongoDB is the store again: the application's users get back the write roles the switch took
  return { kind: 'done', note: await restoreAppWrites(rt) };
}

const RETRY_RELEASE =
  'run `rollback` again: it does only what is left of the release';

const named = (xs: Array<{ name: string; why: string }>): string =>
  xs.map((f) => `${f.name}: ${f.why}`).join('; ');

/**
 * After a hand-over: the carriers let go of what only the move held. Which ones are to let go is written in the job before any of them
 * runs; the list then shrinks to the ones that did not, and a job this rollback ends is ended only after that write (a rollback run
 * again finishes either). `open`: the job stays open for the release (the caller must not end it); the sentence says what is left.
 */
export async function releaseAfterHandOver(
  rt: MigrationRuntime,
  o: { js?: JobState; failAt?: string; phase?: string } = {},
): Promise<{ note: string; open: boolean }> {
  const carriers = carriersFor().filter((c) => c.released !== undefined);
  if (carriers.length === 0) return { note: '', open: false };
  const names = carriers.map((c) => c.name);
  // the online rollback ends its own job (and writes its phase with the record); the offline one ends a job only if it started it here
  let js = o.js;
  let end = Boolean(o.js);
  const beforeSave = (): void => {
    if (hookOn(o.failAt, 'release-record'))
      throw new Error('injected: the release could not be recorded');
  };
  try {
    if (js) {
      const before = { release: js.progress.release, phase: js.progress.phase };
      js.progress.release = { pending: names, end };
      if (o.phase) js.progress.phase = o.phase;
      try {
        beforeSave();
        await js.checkpoint();
      } catch (e) {
        Object.assign(js.progress, before);
        throw e;
      }
    } else {
      const a = await JobState.attachOrStart(rt, {
        releasing: true,
        initial: { release: { pending: names, end: true } },
        beforeSave,
      });
      js = a.js;
      end = a.created;
      if (!end) {
        // a running job this rollback did not start (the move's): named, not ended
        try {
          js.progress.release = { pending: names, end };
          beforeSave();
          await js.checkpoint();
        } catch (e) {
          js.progress.release = undefined;
          throw e;
        }
      }
    }
  } catch (e) {
    // no carrier runs without the record: one that failed would have nothing to retry it
    return {
      note: ` The carriers' release did not start: it could not be recorded (${errText(e)}), so no carrier let go of what the move held in PostgreSQL (${names.join(', ')}): remove what they hold by hand before a move again.`,
      open: false,
    };
  }
  const failed = await releaseCarried(
    carryPg(rt),
    carriers,
    hookOn(o.failAt, 'released'),
  );
  js.progress.release = { pending: failed.map((f) => f.name), end };
  try {
    await js.checkpoint();
  } catch (e) {
    // the job still names every carrier (each release may run twice): in memory too, so that no later save writes a list nobody named
    js.progress.release = { pending: names, end };
    return {
      note: ` The carriers' release could not be saved (${errText(e)})${failed.length ? `, and some did not let go (${named(failed)})` : ''}: job ${String(js.id)} stays open for ${names.join(', ')}: ${RETRY_RELEASE}.`,
      open: true,
    };
  }
  if (failed.length > 0)
    return {
      note: ` Some carriers did not let go of what the move held in PostgreSQL (${named(failed)}); job ${String(js.id)} stays open for ${failed.map((f) => f.name).join(', ')}: ${RETRY_RELEASE}.`,
      open: true,
    };
  if (!end || o.js) return { note: '', open: false }; // the online rollback ends its job itself
  try {
    await js.finish('done');
    return { note: '', open: false };
  } catch (e) {
    return {
      note: ` Every carrier let go, but job ${String(js.id)} could not be ended (${errText(e)}): ${RETRY_RELEASE}.`,
      open: true,
    };
  }
}

/**
 * A rollback run again while a release holds the job: only what is left of it runs (null when nothing does). A pending carrier stays
 * pending until its own step has let go: one this run does not have, or one without the step, is named and kept. The job ends (if it is
 * the rollback's own) only after the emptied list is written.
 */
export async function resumeRelease(
  rt: MigrationRuntime,
  failAt?: string,
): Promise<{ ok: boolean; message: string } | null> {
  const cur = await JobState.current(rt);
  if (!releaseHeld(cur?.progress)) return null;
  const js = await JobState.attach(rt, { releasing: true });
  const rel = js.progress.release ?? { pending: [], end: false };
  const all = carriersFor();
  const kept: Array<{ name: string; why: string }> = [];
  const run: Carrier[] = [];
  for (const name of rel.pending) {
    const c = all.find((x) => x.name === name);
    if (!c) kept.push({ name, why: 'not a carrier of this run' });
    else if (c.released === undefined)
      kept.push({ name, why: 'has no step to let go' });
    else run.push(c);
  }
  kept.push(...(await releaseCarried(carryPg(rt), run)));
  // a retry that only ends the job changes nothing in it: no save stands between it and the end
  if (rel.pending.length > 0) {
    js.progress.release = { pending: kept.map((f) => f.name), end: rel.end };
    await js.checkpoint();
  }
  if (kept.length > 0)
    return {
      ok: false,
      message: `MongoDB is the store; some carriers have not let go of what the move held in PostgreSQL (${named(kept)}): ${RETRY_RELEASE}.`,
    };
  const done = rel.pending.length
    ? `the carriers the rollback left (${rel.pending.join(', ')}) let go of what the move held in PostgreSQL`
    : 'every carrier had let go';
  if (rel.end)
    try {
      if (hookOn(failAt, 'release-finish'))
        throw new Error('injected: the job could not be ended');
      await js.finish('done');
    } catch (e) {
      return {
        ok: false,
        message: `MongoDB is the store; ${done}, but job ${String(js.id)} could not be ended (${errText(e)}): ${RETRY_RELEASE}.`,
      };
    }
  return {
    ok: true,
    message: `MongoDB is the store; ${done}${rel.end ? ' and the job ended' : ''}. Nothing else was run again.`,
  };
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
  const resumed = await resumeRelease(rt, o.failAt);
  if (resumed) return { ...resumed, mode: 'released' };
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
      const { note: released } = await releaseAfterHandOver(rt, {
        failAt: o.failAt,
      });
      return {
        ok: true,
        mode: 'no_pg_writes',
        verify: v,
        message: `PostgreSQL equals MongoDB (job ${String(cur?.id ?? '-')}): nothing was written there. MongoDB is the live store again: set DOC_STORE=mongo and start the application.${h.note}${released}`,
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
    const { note: released } = await releaseAfterHandOver(rt, {
      failAt: o.failAt,
    });
    return {
      ok: true,
      mode: 'reverse',
      verify: after,
      ...r,
      message: `PostgreSQL was copied back and verified: set DOC_STORE=mongo and start the application.${h.note}${released}`,
    };
  } catch (e) {
    if (!state.released) {
      const note = await release();
      if (note && e instanceof Error) e.message += `.${note}`; // the original error stays, with what is left to do
    }
    throw e;
  }
}
