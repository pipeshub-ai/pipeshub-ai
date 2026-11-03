/**
 * The reverse follower (migration design section 8): after the flip, triggers on the document-store tables append every change to
 * `platform.docstore_changes` (migration platform_data_0003). This process reads that log in order and writes the documents it names
 * back to MongoDB, so a rollback after the application has written to PostgreSQL stays possible without stopping the application
 * for a full copy. It runs until the bridge release is retired.
 *
 *  - A log row names a document (a child row names its parent). The follower rebuilds that document from its rows and replaces it in
 *    MongoDB, or removes it when PostgreSQL no longer has it.
 *  - The log is read in id order, but ids are handed out before commit: only rows whose transaction is older than every running one
 *    are taken, and never past a row that is not taken yet.
 *  - Collections that are not one document per row (credentials per address, slug counters, reset links, Slack links) and the
 *    organisations (written by a vetted function, not triggered) are written back by sweeps, at most once per `sweepMs`.
 *  - The outbox is not followed (the dispatcher settles rows all day); `rollbackOnline` copies it once, at the end.
 */
import { Log } from './bulk';
import { clearMarkers } from './follow';
import { MAPPINGS } from './mapping';
import type { CollectionMapping } from './mapping/types';
import {
  modelFor,
  relaxSlugIndexes,
  reverseCollection,
  reverseIds,
} from './reverse';
import { idFilter } from './mongo-read';
import { SPECIAL, reverseSpecial } from './reverse-special';
import type { MigrationRuntime } from './runtime';
import { JobState } from './state';
import { readMongoFence } from './fence';
import {
  BUSY,
  freezePostgres,
  freezeUnknown,
  handOver,
  notPostgresStore,
  releasePostgres,
  whileExclusive,
} from './reverse';
import { VerifyReport, verifyAll } from './verify';

const ROOT = new Map<string, CollectionMapping>();
for (const m of MAPPINGS)
  if (!m.custom && !SPECIAL.has(m.collection)) {
    ROOT.set(m.table, m);
    for (const c of m.children ?? []) ROOT.set(c.table, m);
  }

/** Tables whose changes are written back by a sweep of the whole collection. */
const SWEPT: Record<string, string> = {
  'identity.slug_counters': 'counters',
  'identity.used_reset_links': 'usedPasswordResetLinks',
  'integrations.slack_threads': 'slack_conversations',
};

/** Written back whole at a rollback: not followed row by row (they live minutes) but their MongoDB form must match the row. */
const WRITE_WHOLE = ['authorizationCodes'];

export interface ReverseOptions {
  log: Log;
  /** Stop when nothing new has come for this long (0: run until aborted). */
  untilIdleMs: number;
  stop?: AbortSignal;
  /** Sweeps of the collections that are not followed row by row. */
  sweepMs?: number;
  /** Lag above which the alarm is raised (default `MIGRATE_LAG_ALARM_MS`, 60 s, as for the forward follower). */
  alarmMs?: number;
}

export interface ReverseResult {
  applied: number;
  sweeps: number;
  cursor: number;
  lagMs: number;
  /** Batches written back later than the alarm limit. */
  alarms: number;
}

const ORG_MAPPING = MAPPINGS.find((m) => m.collection === 'org');

async function sweepOrgs(rt: MigrationRuntime): Promise<void> {
  if (!ORG_MAPPING) return;
  const ids = await rt.jobs(
    async (_t, k) =>
      (
        await k.query<{ i: string }>(
          '/* pipeshub:reverse.org_ids */ SELECT org_id AS i FROM identity.orgs',
        )
      ).rows.map((r) => r.i),
    { write: false },
  );
  await reverseIds(rt, ORG_MAPPING, ids);
  // an organisation removed in PostgreSQL is removed in MongoDB too
  const col = rt.db().collection('org');
  const keep = ids.map((i) => i.toLowerCase());
  for await (const d of col.find({}, { projection: { _id: 1 } }))
    if (!keep.includes(String(d._id).toLowerCase()))
      await col.deleteOne({ _id: d._id });
}

type LogRow = {
  id: string;
  rel: string;
  pk: string;
  txid: string;
  age: string;
};

export async function reverseFollow(
  rt: MigrationRuntime,
  js: JobState,
  o: ReverseOptions,
): Promise<ReverseResult> {
  const sweepMs = o.sweepMs ?? 30_000;
  const prog = (js.progress.reverse ??= {
    cursor: '0',
    applied: 0,
    lagMs: 0,
  });
  const res: ReverseResult = {
    applied: 0,
    sweeps: 0,
    cursor: Number(prog.cursor),
    lagMs: 0,
    alarms: 0,
  };
  const alarmMs = o.alarmMs ?? rt.cfg.lagAlarmMs;
  let lastAlarm = 0;
  const due = new Set<string>();
  let lastSweep = 0;
  let idleSince = Date.now();
  const noteLag = (ageMs: number): void => {
    res.lagMs = ageMs;
    prog.lagMs = ageMs;
    if (res.lagMs > alarmMs) {
      prog.alarm = true;
      res.alarms += 1;
      if (Date.now() - lastAlarm > 60_000) {
        lastAlarm = Date.now();
        o.log('reverse_lag_alarm', {
          lagMs: res.lagMs,
          alarmMs,
          m: 'the change log is being written back late: MongoDB is behind PostgreSQL by more than the alarm limit',
        });
      }
    } else delete prog.alarm;
  };
  const applyRows = async (rows: LogRow[]): Promise<void> => {
    const byColl = new Map<
      string,
      { m: CollectionMapping; ids: Set<string> }
    >();
    for (const r of rows) {
      const swept = SWEPT[r.rel];
      if (swept) {
        due.add(swept);
        continue;
      }
      const m = ROOT.get(r.rel);
      if (!m) continue;
      const e = byColl.get(m.collection) ?? { m, ids: new Set<string>() };
      e.ids.add(r.pk);
      byColl.set(m.collection, e);
    }
    for (const { m, ids } of byColl.values()) await reverseIds(rt, m, [...ids]);
  };
  // The rows of these collections are trimmed from the log as soon as they are read, so what was queued must be written before this returns,
  // whichever way it ends (idle, aborted, or an error): the next run could not know it was owed.
  const flushDue = async (): Promise<void> => {
    for (const name of [...due]) {
      await reverseSpecial(rt, name);
      res.sweeps += 1;
      due.delete(name);
    }
  };
  await relaxSlugIndexes(rt, o.log);
  try {
    while (!o.stop?.aborted) {
      // The cursor is a transaction id, not a log id: log ids are handed out when a row is inserted but become visible when its
      // transaction commits, so a transaction that inserted id 100 and commits after one that inserted id 101 would be skipped by an
      // id cursor for good. Every transaction below the snapshot's xmin has finished, so everything it wrote is visible now and
      // nothing with a lower transaction id can appear later. (The document is rebuilt from its rows, so the order does not matter.)
      const PAGE = 2000;
      const readLog = (
        where: string,
        args: string[],
        limit: number,
      ): Promise<LogRow[]> =>
        rt.jobs(
          async (_t, k) =>
            (
              await k.query<LogRow>(
                `/* pipeshub:reverse.log_read */ SELECT id::text, rel, pk, txid::text, (extract(epoch FROM clock_timestamp() - at) * 1000)::bigint::text AS age FROM platform.docstore_changes WHERE ${where} ORDER BY txid, id LIMIT ${String(limit)}`,
                args,
              )
            ).rows,
          { write: false },
        );
      let rows = await readLog(
        'txid > $1::bigint AND txid < pg_snapshot_xmin(pg_current_snapshot())::text::bigint',
        [prog.cursor],
        PAGE,
      );
      const full = rows.length === PAGE;
      if (full) {
        // a page ends between transactions: the last one is read again whole on the next page (or alone, if it fills a page itself)
        const lastTx = (rows[rows.length - 1] as LogRow).txid;
        const whole = rows.filter((r) => r.txid !== lastTx);
        if (whole.length) rows = whole;
        else {
          // one transaction fills a page by itself: write it back page by page, trimming only what was written, and move the cursor
          // past it once its last row is done (a crash in between reads the rest again; nothing is dropped unread)
          let afterId = '0';
          let n = 0;
          let age = '0';
          for (;;) {
            const page = await readLog(
              'txid = $1::bigint AND id > $2::bigint',
              [lastTx, afterId],
              PAGE,
            );
            if (page.length === 0) break;
            await applyRows(page);
            afterId = (page[page.length - 1] as LogRow).id;
            n += page.length;
            age = (page[page.length - 1] as LogRow).age;
            await rt.jobs(
              (_t, k) =>
                k.query(
                  '/* pipeshub:reverse.log_trim_tx */ DELETE FROM platform.docstore_changes WHERE txid = $1::bigint AND id <= $2::bigint',
                  [lastTx, afterId],
                ),
              { write: true },
            );
            if (page.length < PAGE) break;
          }
          await rt.jobs(
            (_t, k) =>
              k.query(
                '/* pipeshub:reverse.log_trim */ DELETE FROM platform.docstore_changes WHERE txid <= $1::bigint',
                [lastTx],
              ),
            { write: true },
          );
          prog.cursor = lastTx;
          prog.applied += n;
          res.applied += n;
          res.cursor = Number(lastTx);
          noteLag(Number(age));
          await js.checkpoint();
          idleSince = Date.now();
          continue;
        }
      }
      const taken = rows;
      if (taken.length > 0) {
        await applyRows(taken);
        const last = taken[taken.length - 1] as (typeof taken)[number];
        await rt.jobs(
          (_t, k) =>
            k.query(
              '/* pipeshub:reverse.log_trim */ DELETE FROM platform.docstore_changes WHERE txid <= $1::bigint',
              [last.txid],
            ),
          { write: true },
        );
        prog.cursor = last.txid;
        prog.applied += taken.length;
        res.applied += taken.length;
        res.cursor = Number(last.txid);
        noteLag(Number(last.age));
        await js.checkpoint();
        o.log('reverse_applied', { rows: taken.length, lagMs: res.lagMs });
        idleSince = Date.now();
        if (full) continue; // a full page: more is waiting
      } else {
        res.lagMs = 0;
        prog.lagMs = 0;
        delete prog.alarm;
      }
      if (Date.now() - lastSweep >= sweepMs) {
        lastSweep = Date.now();
        due.add('userCredentials');
        await flushDue();
        await sweepOrgs(rt);
      }
      if (taken.length === 0) {
        if (o.untilIdleMs > 0 && Date.now() - idleSince >= o.untilIdleMs) break;
        await new Promise((r) => setTimeout(r, 200));
      }
    }
  } catch (e) {
    await flushDue().catch(() => undefined); // the error is the one to see
    throw e;
  }
  await flushDue();
  return res;
}

export interface OnlineRollbackReport {
  ok: boolean;
  stage: string;
  message: string;
  verify?: VerifyReport;
  applied?: number;
  freezeMs?: number;
}

/**
 * Rollback after the application has written to PostgreSQL (migration design section 8): freeze PostgreSQL (the exclusive barrier
 * lock and `readonly` in one transaction), drain the change log, write the collections that are swept and the outbox once more,
 * verify in the reverse direction, then make MongoDB the store again at a new epoch. A failed step unfreezes PostgreSQL: it stays the store.
 */
export async function rollbackOnline(
  rt: MigrationRuntime,
  o: { log: Log; sample?: number; maxFreezeMs: number; failAt?: string },
): Promise<OnlineRollbackReport> {
  return whileExclusive<OnlineRollbackReport>(
    rt,
    () => ({ ok: false, stage: 'busy', message: BUSY }),
    () => rollbackOnlineLocked(rt, o),
  );
}

async function rollbackOnlineLocked(
  rt: MigrationRuntime,
  o: { log: Log; sample?: number; maxFreezeMs: number; failAt?: string },
): Promise<OnlineRollbackReport> {
  const t0 = Date.now();
  let previous = false;
  const state = { released: false };
  const unfreezePg = (): Promise<string> => {
    state.released = true;
    return releasePostgres(rt, previous, o.failAt);
  };
  // what is true of PostgreSQL after the release: the value the freeze found is back, not always "writable"
  const restored = (note: string): string =>
    note ||
    (previous
      ? "PostgreSQL is read-only again, as it was when this rollback started, and remains the store (if an earlier attempt froze it, reopen it with `UPDATE platform.store_epoch SET readonly = false WHERE backend = 'postgres'`)."
      : 'PostgreSQL is writable again and remains the store.');
  const notStore = await notPostgresStore(rt);
  if (notStore) return { ok: false, stage: 'fence', message: notStore };
  const fenceBefore = await readMongoFence(rt);
  if (fenceBefore.mode !== 'retired')
    return {
      ok: false,
      stage: 'fence',
      message: `the MongoDB fence is '${fenceBefore.mode}': it was not retired by a move to PostgreSQL`,
    };
  // a refusal above starts no job; a job this call started is finished if the freeze fails, so the next run starts fresh
  const { js, created } = await JobState.attachOrStart(rt);
  const frozen = await freezePostgres(rt, o.failAt);
  if (frozen.outcome !== 'applied') {
    if (frozen.outcome === 'not_applied') {
      if (created) await js.finish('failed').catch(() => undefined);
      return {
        ok: false,
        stage: 'freeze',
        message: `PostgreSQL could not be frozen: ${frozen.why}. Nothing was changed`,
      };
    }
    return {
      ok: false,
      stage: 'freeze',
      message: freezeUnknown(frozen.why),
    };
  }
  previous = frozen.previous;
  const overBudget = (): boolean => Date.now() - t0 > o.maxFreezeMs;
  // how far the hand-over got decides what a failure may do: once MongoDB may be live again PostgreSQL stays read-only and is never unfrozen
  let stage: 'before' | 'handed' = 'before';
  try {
    const r = await reverseFollow(rt, js, {
      log: o.log,
      untilIdleMs: 1500,
      sweepMs: 0,
    });
    // what is swept rather than followed row by row is written once more inside the freeze, then the outbox
    for (const name of new Set(Object.values(SWEPT)))
      await reverseSpecial(rt, name);
    await reverseSpecial(rt, 'outbox_events');
    // short-lived documents whose MongoDB form differs from the row until written back (codes come back as their hashes)
    for (const name of WRITE_WHOLE) {
      const m = MAPPINGS.find((x) => x.collection === name);
      const model = modelFor(name);
      if (m && model)
        await reverseCollection(
          rt,
          m,
          model,
          Object.keys(js.progress.collections[name]?.skippedIds ?? {}).map(
            (id) => idFilter(id),
          ),
        );
    }
    const budget = async (): Promise<OnlineRollbackReport> => {
      const note = await unfreezePg();
      return {
        ok: false,
        stage: 'budget',
        message: `the freeze budget was used up; ${restored(note)}`,
      };
    };
    if (overBudget()) return await budget();
    const v = await verifyAll(rt, {
      log: o.log,
      afterReverse: true,
      ...(o.sample !== undefined ? { sample: o.sample } : {}),
    });
    if (!v.ok) {
      const note = await unfreezePg();
      return {
        ok: false,
        stage: 'verify',
        verify: v,
        applied: r.applied,
        message: `the reverse copy does not verify; ${restored(note)}`,
      };
    }
    // MongoDB is not live yet, so a budget spent by the verify can still be undone
    if (
      overBudget() ||
      (o.failAt === 'over-after-verify' &&
        process.env.MIGRATE_TEST_HOOKS === '1')
    )
      return await budget();
    stage = 'handed';
    const h = await handOver(rt, fenceBefore.epoch + 1, o.failAt);
    if (h.kind === 'stuck')
      return { ok: false, stage: 'finalize', message: h.message };
    if (h.kind === 'not_live') {
      stage = 'before';
      throw new Error(h.why);
    }
    // the move is over: end the job (the import login's exemption closes with it) and leave no markers behind
    try {
      if (
        o.failAt === 'finalize-checkpoint' &&
        process.env.MIGRATE_TEST_HOOKS === '1'
      )
        throw new Error('injected failure after the store row');
      js.progress.phase = 'rolled_back';
      await js.checkpoint();
      await js.finish('done');
      await clearMarkers(rt);
    } catch (e) {
      return {
        ok: true,
        stage: 'done',
        freezeMs: Date.now() - t0,
        message: `MongoDB is the store again at a new epoch and PostgreSQL is closed, but the job record could not be finished (${e instanceof Error ? e.message : String(e)}): end the job and remove the follower's markers by hand, then roll the application back to DOC_STORE=mongo now`,
      };
    }
    return {
      ok: true,
      stage: 'done',
      verify: v,
      applied: r.applied,
      freezeMs: Date.now() - t0,
      message:
        'MongoDB is the store again at a new epoch and PostgreSQL is closed: roll the application back to DOC_STORE=mongo now',
    };
  } catch (e) {
    const why = e instanceof Error ? e.message : String(e);
    if (stage === 'handed')
      return {
        ok: false,
        stage: 'finalize',
        message: `the hand-over failed (${why}). PostgreSQL stays read-only and was not unfrozen: read platform.store_epoch and the MongoDB fence before any manual write`,
      };
    const note = state.released ? '' : await unfreezePg();
    return {
      ok: false,
      stage: 'error',
      message: `the rollback failed (${why}); ${restored(note)}`,
    };
  }
}
