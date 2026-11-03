import { ensurePgRuntime } from '../../../libs/db/runtime-boot';
import { usesInProcessCache } from '../../../libs/services/redis.service';

/**
 * The crawl scheduler's backend. BullMQ on Redis stays the default when a Redis is configured; with none
 * (`CRAWL_SCHEDULER=postgres`, or the key-value store and the broker both PostgreSQL) the jobs, schedules and the
 * reconcile-pending marks live in PostgreSQL, claimed with SKIP LOCKED so several Node processes never fire a run twice.
 */
export function usesPgScheduler(): boolean {
  const forced = (process.env.CRAWL_SCHEDULER ?? '').toLowerCase();
  if (forced === 'postgres') return true;
  if (forced === 'bullmq') return false;
  return usesInProcessCache();
}

const PREFIX = 'crawl:reconcile:';
const sql = {
  mark: `/* pipeshub:crawl.mark */ INSERT INTO platform.kv_entries (key, value, updated_at) VALUES ($1, $2, now())
    ON CONFLICT (key) DO UPDATE SET value = excluded.value, expires_at = NULL, updated_at = now()`,
  get: `/* pipeshub:crawl.get */ SELECT convert_from(value, 'UTF8') AS v FROM platform.kv_entries WHERE key = $1`,
  clear: `/* pipeshub:crawl.clear */ DELETE FROM platform.kv_entries WHERE key = $1 AND value = $2`,
  list: `/* pipeshub:crawl.list */ SELECT key, convert_from(value, 'UTF8') AS v FROM platform.kv_entries WHERE starts_with(key, $1) ORDER BY key`,
};

const unit = <R>(
  write: boolean,
  fn: (tx: import('../../../libs/db/uow').Tx) => Promise<R>,
): Promise<R> => ensurePgRuntime().withUow({ preOrg: true, write }, fn);

/** Connector ids whose saved settings may not have reached their schedule; survives a restart. */
export const pgReconcilePending = {
  async mark(connectorId: string, orgId: string): Promise<void> {
    await unit(true, (tx) =>
      tx.query(sql.mark, [
        PREFIX + connectorId,
        Buffer.from(JSON.stringify({ orgId, at: Date.now() })),
      ]),
    );
  },

  /** Clears the mark unless a save after `since` set it again; deleted only if it is still the mark just read. */
  async clear(connectorId: string, since: number): Promise<void> {
    await unit(true, async (tx) => {
      const r = await tx.query<{ v: string }>(sql.get, [PREFIX + connectorId]);
      const raw = r.rows[0]?.v;
      if (raw === undefined || (JSON.parse(raw) as { at: number }).at > since)
        return;
      await tx.query(sql.clear, [PREFIX + connectorId, Buffer.from(raw)]);
    });
  },

  async list(): Promise<{ connectorId: string; orgId: string; at: number }[]> {
    const r = await unit(false, (tx) =>
      tx.query<{ key: string; v: string }>(sql.list, [PREFIX]),
    );
    return r.rows.map((x) => ({
      connectorId: x.key.slice(PREFIX.length),
      ...(JSON.parse(x.v) as { orgId: string; at: number }),
    }));
  },
};
