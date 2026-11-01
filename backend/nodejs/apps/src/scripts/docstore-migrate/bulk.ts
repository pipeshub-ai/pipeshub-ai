/**
 * Bulk copy: every mapped collection, parents first, through the repositories' `importBatch`. A checkpoint (the last `_id`) is
 * saved in `platform.jobs` after every batch, so a killed run continues where it stopped; rows already written are left alone
 * (`ON CONFLICT DO NOTHING`), so a batch that was written but not checkpointed is harmless.
 */
import type { Db } from 'mongodb';
import { REPOSITORIES } from '../../libs/repositories/all';
import type { Importable } from '../../libs/repositories/import';
import { runFollowUps } from './follow-ups';
import { MAPPINGS } from './mapping';
import type { CollectionMapping } from './mapping/types';
import { winningCredentials } from './credentials';
import { idString, readBatches } from './mongo-read';
import type { MigrationRuntime } from './runtime';
import { SourceReport, toNeutral } from './source';
import type { CollectionProgress, JobState } from './state';

export type Log = (event: string, data?: Record<string, unknown>) => void;

export interface BulkOptions {
  only?: string[];
  /** Test hook: stop (throw) after this many batches, as a killed process would. */
  stopAfterBatches?: number;
  log: Log;
}

/** Database a collection is read from. */
export const sourceDb = (rt: MigrationRuntime, m: CollectionMapping): Db =>
  rt.db(
    m.collection === 'slack_conversations' && rt.cfg.slackDb
      ? rt.cfg.slackDb
      : undefined,
  );

export function repositoryFor(m: CollectionMapping): Importable {
  const repo = REPOSITORIES[m.repository]?.() as Importable | undefined;
  if (!repo || typeof repo.importBatch !== 'function')
    throw new Error(
      `${m.collection}: repository '${m.repository}' has no importBatch`,
    );
  return repo;
}

/** Skipped documents kept by id in the job state (64 KB budget); a verify that needs more says so. */
export const MAX_SKIPPED_LISTED = 300;

export class StoppedForTest extends Error {}

/**
 * `_id` ranges for the workers of a big collection (null: copy it in one stream). The split points are the ids at equal
 * positions of the `_id` index, so the ranges hold about the same number of documents. Saved in the job state, so a restart
 * continues the same ranges.
 */
async function splitRanges(
  rt: MigrationRuntime,
  m: CollectionMapping,
  db: Db,
): Promise<NonNullable<CollectionProgress['ranges']> | undefined> {
  const w = rt.cfg.parallel;
  if (w < 2 || m.oneBatch || m.sourceKey || m.custom) return undefined;
  const n = await db.collection(m.collection).estimatedDocumentCount();
  if (n < rt.cfg.parallelMinDocs) return undefined;
  const cuts: string[] = [];
  for (let k = 1; k < w; k++) {
    const [d] = await db
      .collection(m.collection)
      .find({}, { projection: { _id: 1 }, promoteLongs: false })
      .sort({ _id: 1 })
      .hint({ _id: 1 })
      .skip(Math.floor((k * n) / w))
      .limit(1)
      .toArray();
    if (d) cuts.push(idString(d._id));
  }
  const bounds: Array<string | null> = [null, ...cuts, null];
  return bounds.slice(0, -1).map((from, i) => ({
    from,
    to: bounds[i + 1] ?? null,
    last: null,
    done: false,
  }));
}

export async function bulk(
  rt: MigrationRuntime,
  js: JobState,
  opts: BulkOptions,
): Promise<{ report: SourceReport }> {
  const report = new SourceReport();
  let batches = 0;
  let failed = false;
  js.progress.phase = 'bulk';
  await js.checkpoint();
  for (const m of MAPPINGS) {
    if (opts.only && !opts.only.includes(m.collection)) continue;
    const p = (js.progress.collections[m.collection] ??= {
      state: 'pending',
      last: null,
      copied: 0,
      skipped: 0,
      rows: {},
    });
    if (p.state === 'done') continue;
    p.state = 'running';
    const repo = repositoryFor(m);
    const db = sourceDb(rt, m);
    // credentials: only the newest of each address is offered (see credentials.ts)
    const winners =
      m.collection === 'userCredentials'
        ? await winningCredentials(rt)
        : undefined;
    opts.log('collection_start', { collection: m.collection, after: p.last });
    const copyRange = async (
      range: NonNullable<CollectionProgress['ranges']>[number] | null,
    ): Promise<void> => {
      const after = range ? (range.last ?? range.from) : p.last;
      for await (const b of readBatches(
        db,
        m.collection,
        after,
        m.oneBatch ? 500_000 : rt.cfg.batchSize,
        m.oneBatch ? 512 * 1024 * 1024 : rt.cfg.batchBytes,
        range?.to ?? undefined,
      )) {
        if (failed) return;
        const rep = new SourceReport();
        const offered = winners
          ? b.docs.filter((d) => winners.has(String(d._id).toLowerCase()))
          : b.docs;
        const docs = offered.map((d) => toNeutral(d, rep, m.collection));
        const stats = docs.length
          ? await rt.jobs((tx) => repo.importBatch(m.collection, docs, tx))
          : { skipped: [], inserted: {}, followUps: [] };
        await runFollowUps(rt, stats.followUps);
        report.merge(rep);
        if (range) range.last = b.last;
        else p.last = b.last;
        p.copied += docs.length - stats.skipped.length;
        p.skipped += stats.skipped.length + (b.docs.length - offered.length);
        for (const sk of stats.skipped) {
          p.skippedIds ??= {};
          if (Object.keys(p.skippedIds).length < MAX_SKIPPED_LISTED)
            p.skippedIds[sk.id] = sk.reason;
        }
        for (const [t, n] of Object.entries(stats.inserted))
          p.rows[t] = (p.rows[t] ?? 0) + n;
        for (const [k, n] of rep.nul)
          p.rows[`nul:${k}`] = (p.rows[`nul:${k}`] ?? 0) + n;
        await js.checkpoint();
        batches += 1;
        // test aid: slow batches give a drill time to kill the process between two checkpoints
        if (rt.cfg.batchDelayMs > 0)
          await new Promise((r) => setTimeout(r, rt.cfg.batchDelayMs));
        if (opts.stopAfterBatches && batches >= opts.stopAfterBatches)
          throw new StoppedForTest(`stopped after ${String(batches)} batches`);
      }
      if (range) range.done = true;
    };
    if (!p.ranges && !p.last) p.ranges = await splitRanges(rt, m, db);
    if (p.ranges) {
      const ranges = p.ranges;
      // one worker failing stops the others at their next batch; all of them are waited for, so none is still writing when this returns
      const settled = await Promise.allSettled(
        ranges
          .filter((r) => !r.done)
          .map((r) =>
            copyRange(r).catch((e: unknown) => {
              failed = true;
              throw e;
            }),
          ),
      );
      const bad = settled.find((x) => x.status === 'rejected');
      if (bad) throw bad.reason;
      p.last = ranges.reduce<string | null>(
        (a, r) => (r.last && (!a || r.last > a) ? r.last : a),
        null,
      );
    } else await copyRange(null);
    p.state = 'done';
    await js.checkpoint();
    opts.log('collection_done', {
      collection: m.collection,
      copied: p.copied,
      skipped: p.skipped,
    });
  }
  if (!opts.only) {
    js.progress.phase = 'bulk_done';
    await js.checkpoint();
  }
  return { report };
}
