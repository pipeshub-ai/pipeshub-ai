/**
 * Follow mode (migration design section 4): MongoDB change streams, `client.watch()` over the API and Slack databases with
 * `fullDocument: 'updateLookup'`. Every event is applied unconditionally in stream order (an upsert replaces all of a
 * document's rows in one unit, a delete removes them), only once its cluster time is at or below the majority commit point, and
 * the resume token is saved after each applied batch. Opened before the bulk copy (`follow start`), so nothing between is missed.
 *
 * For real use it also: measures its lag (an alarm in the job state and the log above `MIGRATE_LAG_ALARM_MS`, default 60 s);
 * recovers when the saved token has fallen out of the oplog (a fresh token, then a full compare and an id-diff repair, repeated
 * until clean); can run until a marker document it wrote has come through the stream (everything written before the marker has
 * then been applied: the final catch-up of the freeze); and counts the application's events after the freeze (the flip needs zero).
 */
import { ChangeStream, ChangeStreamDocument, Timestamp } from 'mongodb';
import { deleteDocs, replaceDocs } from './apply';
import { Log } from './bulk';
import type { DocDiff } from './compare';
import { MAPPINGS } from './mapping';
import type { CollectionMapping } from './mapping/types';
import { syncAllCredentials, syncCredentials } from './credentials';
import { repair } from './repair';
import type { MigrationRuntime } from './runtime';
import type { JobState } from './state';
import { verifyAll } from './verify';

const BY_NAME = new Map(MAPPINGS.map((m) => [m.collection, m]));
const USERS = MAPPINGS.find(
  (m) => m.collection === 'users',
) as CollectionMapping;

/** Collection the migrator writes its own markers to (and the fence lives in): not application events. */
export const MARKER_COLLECTION = 'docstore_migrate_marker';

/** Removes the marker documents (they are only the follower's own sync points): a finished job leaves nothing in the application database. */
export async function clearMarkers(rt: MigrationRuntime): Promise<void> {
  await rt
    .db()
    .collection(MARKER_COLLECTION)
    .drop()
    .catch(() => undefined);
}
const FENCE_COLLECTION = 'docstore_fence';

export function watchOptions(resumeAfter?: unknown): Record<string, unknown> {
  return {
    fullDocument: 'updateLookup',
    promoteLongs: false,
    ...(resumeAfter ? { resumeAfter } : {}),
  };
}

const databases = (rt: MigrationRuntime): string[] => [
  rt.cfg.mongoDb,
  ...(rt.cfg.slackDb ? [rt.cfg.slackDb] : []),
];

function openStream(rt: MigrationRuntime, resume?: unknown): ChangeStream {
  return rt.mongo.watch(
    [{ $match: { 'ns.db': { $in: databases(rt) } } }],
    watchOptions(resume),
  );
}

/** The majority commit point (cluster time) of the replica set. */
export async function majorityPoint(
  rt: MigrationRuntime,
): Promise<Timestamp | null> {
  const s = await rt.mongo.db('admin').command({ replSetGetStatus: 1 });
  return (s.optimes?.lastCommittedOpTime?.ts as Timestamp | undefined) ?? null;
}

/** Server error codes of a change stream that cannot be resumed: history lost (286), fatal (280), resume token not found (260/261/ 40585). */
const HISTORY_LOST = new Set([280, 286, 260, 261, 40585]);
export const isHistoryLost = (e: unknown): boolean => {
  const x = e as { code?: number; message?: string } | null;
  return (
    HISTORY_LOST.has(x?.code ?? -1) ||
    /resume token was not found|may no longer be in the oplog/i.test(
      x?.message ?? '',
    )
  );
};

/** Saves a resume token that is "now", before the copy starts. `force` replaces a token that exists (after history was lost). */
export async function followStart(
  rt: MigrationRuntime,
  js: JobState,
  force = false,
): Promise<void> {
  if (!force && js.progress.follow?.resumeToken) return;
  const stream = openStream(rt);
  try {
    await stream.tryNext();
    js.progress.follow = {
      resumeToken: stream.resumeToken ?? null,
      appliedUntil: null,
      events: js.progress.follow?.events ?? 0,
      ...(js.progress.follow?.resyncs
        ? { resyncs: js.progress.follow.resyncs }
        : {}),
    };
    await js.checkpoint();
  } finally {
    await stream.close();
  }
}

export interface FollowOptions {
  /** Stop after the stream has been idle this long (final catch-up, tests). 0: run until `stop` is aborted. */
  untilIdleMs: number;
  stop?: AbortSignal;
  log: Log;
  /** Lag above which the alarm is raised (default `MIGRATE_LAG_ALARM_MS`, 60 s, from the config). */
  alarmMs?: number;
  /** Return once the event for this marker `_id` has been applied (the marker was written to MARKER_COLLECTION). */
  untilMarker?: string;
  /**
   * Count the events of the application after this cluster time (the fence's own and the markers are not counted). An event within
   * `stragglerSeconds` of it is a write admitted just before the freeze took hold (the wrapper trusts an `active` answer for a moment):
   * it is applied and counted as a straggler. A later one means the freeze leaked.
   */
  watchAfter?: Timestamp;
  stragglerSeconds?: number;
  /** Called with the ids of every applied document (collection -> ids): the incremental verify's worklist. */
  onApplied?: (collection: string, ids: string[]) => void;
  /** Return when the stream is idle and the majority point has reached this time. */
  untilTime?: Timestamp;
}

export interface FollowResult {
  applied: number;
  lagMs: number;
  markerSeen: boolean;
  /** Events on mapped collections later than the straggler window after `watchAfter`. */
  appEventsAfter: number;
  /** Events inside the straggler window (applied, not a violation). */
  stragglers: number;
  /** Times the saved token was lost and the copy was brought level by comparing. */
  resyncs: number;
  /** Largest lag seen, and how many batches were above the alarm limit. */
  maxLagMs: number;
  alarms: number;
}

type Apply = {
  m: CollectionMapping;
  upserts: Map<string, unknown>;
  deletes: Set<string>;
};

const lagOf = (t: Timestamp | undefined): number =>
  t ? Math.max(0, Date.now() - t.high * 1000) : 0;

/** Brings the copy level after the saved token has fallen out of the oplog: new token, full compare, repair, until clean. */
export async function resync(
  rt: MigrationRuntime,
  js: JobState,
  log: Log,
): Promise<void> {
  log('follow_history_lost', {
    m: 'the saved resume token is no longer in the oplog: starting again from now and comparing everything',
  });
  await followStart(rt, js, true);
  await syncAllCredentials(rt);
  for (let pass = 1; pass <= 4; pass++) {
    const diffs = new Map<string, DocDiff[]>();
    const rep = await verifyAll(rt, {
      log,
      onDiff: (c, d) => {
        const list = diffs.get(c) ?? [];
        list.push(d);
        diffs.set(c, list);
      },
    });
    if (diffs.size === 0 && rep.ok) break;
    const r = await repair(rt, diffs, log);
    log('follow_repaired', { pass, ...r });
    if (r.replaced + r.deleted === 0) break;
  }
  if (js.progress.follow)
    js.progress.follow.resyncs = (js.progress.follow.resyncs ?? 0) + 1;
  await js.checkpoint();
}

export async function follow(
  rt: MigrationRuntime,
  js: JobState,
  o: FollowOptions,
): Promise<FollowResult> {
  if (!js.progress.follow?.resumeToken)
    throw new Error('no resume token: run `follow start` before the copy');
  const alarmMs = o.alarmMs ?? rt.cfg.lagAlarmMs;
  const res: FollowResult = {
    applied: 0,
    lagMs: 0,
    markerSeen: false,
    appEventsAfter: 0,
    stragglers: 0,
    resyncs: 0,
    maxLagMs: 0,
    alarms: 0,
  };
  let idleSince = Date.now();
  let lastAlarm = 0;
  let stream = openStream(rt, js.progress.follow.resumeToken);
  const reopen = async (): Promise<void> => {
    await stream.close().catch(() => undefined);
    stream = openStream(rt, js.progress.follow?.resumeToken);
  };
  try {
    while (!o.stop?.aborted) {
      const batch: Array<ChangeStreamDocument<Record<string, unknown>>> = [];
      try {
        const first = await stream.tryNext();
        if (first) {
          batch.push(first);
          for (let i = 0; i < 499; i++) {
            const n = await stream.tryNext();
            if (!n) break;
            batch.push(n);
          }
        }
      } catch (e) {
        if (!isHistoryLost(e)) throw e;
        await stream.close().catch(() => undefined);
        await resync(rt, js, o.log);
        res.resyncs += 1;
        stream = openStream(rt, js.progress.follow.resumeToken);
        continue;
      }
      if (batch.length === 0) {
        res.lagMs = 0;
        if (js.progress.follow.lagMs) {
          js.progress.follow.lagMs = 0;
          delete js.progress.follow.alarm;
        }
        const point =
          o.untilTime && (await majorityPoint(rt).catch(() => null));
        if (o.untilTime && point && !point.lessThan(o.untilTime)) break;
        if (o.untilIdleMs > 0 && Date.now() - idleSince >= o.untilIdleMs) break;
        await new Promise((r) => setTimeout(r, 200));
        continue;
      }
      idleSince = Date.now();
      // majority only: hold events past the majority commit point (the stream keeps them; our token stays behind)
      const point = await majorityPoint(rt).catch(() => null);
      const ready = point
        ? batch.filter((e) => !(e.clusterTime as Timestamp).greaterThan(point))
        : batch;
      const held = ready.length < batch.length;
      if (held) {
        o.log('follow_holding', { held: batch.length - ready.length });
        if (ready.length === 0) {
          await new Promise((r) => setTimeout(r, 500));
          await reopen();
          continue;
        }
      }
      // one entry per collection in the batch: the last event for a document wins, and a batch is applied in two phases (deletes
      // children first, then writes parents first), so a batch of 500 events is a handful of units, not 500
      const pending = new Map<CollectionMapping, Apply>();
      const credentials: string[] = [];
      let credentialDeleted = false;
      const flush = async (): Promise<void> => {
        const todo = MAPPINGS.filter((m) => pending.has(m));
        const users = pending.get(USERS);
        for (const m of [...todo].reverse()) {
          const c = pending.get(m) as Apply;
          if (c.deletes.size)
            await rt.jobs((_tx, k) => deleteDocs(k, m, [...c.deletes]));
        }
        for (const m of todo) {
          const c = pending.get(m) as Apply;
          if (c.upserts.size) await replaceDocs(rt, m, [...c.upserts.values()]);
          o.onApplied?.(m.collection, [...c.upserts.keys(), ...c.deletes]);
        }
        // a credential row hangs on its account: written from the credentials in MongoDB for the users whose credential changed and
        // for the accounts replaced or removed in this batch (the rows of a removed account go with it)
        const accounts = new Set<string>(credentials);
        for (const id of [
          ...(users?.upserts.keys() ?? []),
          ...(users?.deletes ?? []),
        ])
          accounts.add(id);
        if (credentialDeleted) await syncAllCredentials(rt);
        else await syncCredentials(rt, accounts);
        pending.clear();
        credentials.length = 0;
        credentialDeleted = false;
      };
      let lastTime: Timestamp | undefined;
      for (const e of ready) {
        if (!('ns' in e)) continue;
        const coll = (e.ns as { coll: string }).coll;
        lastTime = e.clusterTime;
        if (coll === MARKER_COLLECTION) {
          if (
            o.untilMarker &&
            'documentKey' in e &&
            String((e.documentKey as { _id: unknown })._id) === o.untilMarker
          )
            res.markerSeen = true;
          continue;
        }
        if (!('documentKey' in e)) continue;
        const m = BY_NAME.get(coll);
        if (
          o.watchAfter &&
          coll !== FENCE_COLLECTION &&
          m &&
          (e.clusterTime as Timestamp).greaterThan(o.watchAfter)
        ) {
          if (
            (e.clusterTime as Timestamp).high - o.watchAfter.high <=
            (o.stragglerSeconds ?? 3)
          )
            res.stragglers += 1;
          else res.appEventsAfter += 1;
        }
        if (!m) continue;
        if (m.collection === 'userCredentials') {
          // a credential row is written after the batch from the user's credentials in MongoDB (see flush)
          if (e.operationType === 'delete') credentialDeleted = true;
          else if ('fullDocument' in e && e.fullDocument) {
            const u = (e.fullDocument as { userId?: unknown }).userId;
            if (typeof u === 'string') credentials.push(u);
          }
          res.applied += 1;
          continue;
        }
        let current = pending.get(m);
        if (!current) {
          current = { m, upserts: new Map(), deletes: new Set() };
          pending.set(m, current);
        }
        const id = String(
          (e.documentKey as { _id: unknown })._id,
        ).toLowerCase();
        if (e.operationType === 'delete') {
          current.upserts.delete(id);
          current.deletes.add(id);
        } else if ('fullDocument' in e && e.fullDocument) {
          current.deletes.delete(id);
          current.upserts.set(id, e.fullDocument);
        }
        res.applied += 1;
      }
      await flush();
      res.lagMs = lagOf(lastTime);
      const alarm = res.lagMs > alarmMs;
      res.maxLagMs = Math.max(res.maxLagMs, res.lagMs);
      if (alarm) res.alarms += 1;
      js.progress.follow = {
        // the token is the last event applied (every event carries its own as `_id`): held events are read again from there
        resumeToken: held
          ? (ready[ready.length - 1] as { _id: unknown })._id
          : stream.resumeToken,
        appliedUntil: new Date().toISOString(),
        events: js.progress.follow.events + ready.length,
        lagMs: res.lagMs,
        ...(alarm ? { alarm: true } : {}),
        ...(js.progress.follow.resyncs
          ? { resyncs: js.progress.follow.resyncs }
          : {}),
      };
      await js.checkpoint();
      if (alarm && Date.now() - lastAlarm > 60_000) {
        lastAlarm = Date.now();
        o.log('follow_lag_alarm', {
          lagMs: res.lagMs,
          alarmMs,
          m: 'the copy is behind MongoDB by more than the alarm limit (a data node down, or a burst of writes)',
        });
      }
      o.log('follow_applied', { events: ready.length, lagMs: res.lagMs });
      if (held) {
        await new Promise((r) => setTimeout(r, 200));
        await reopen();
      }
      if (o.untilMarker && res.markerSeen) break;
    }
  } finally {
    await stream.close().catch(() => undefined);
  }
  return res;
}
