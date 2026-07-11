import { inject, injectable } from 'inversify';
import { Redis } from 'ioredis';
import { RedisService } from '../../../libs/services/redis.service';
import { Logger } from '../../../libs/services/logger.service';
import { NotificationService } from '../../notification/service/notification.service';
import { classify } from '../constants/progress-status.constant';
import { ConnectorProgress, ProgressSnapshot } from '../types/progress.types';

const TICK_MS = parseInt(process.env.PROGRESS_TICK_MS || '5000', 10);
const HEARTBEAT_STALE_MS = 90_000; // indexer considered dead past this
// ETA rate is measured over this rolling window, NOT per tick — a single 5s tick
// is far shorter than one record's processing time, so most ticks see 0 settle.
const ETA_WINDOW_SECONDS = 20;
// A seed/reconcile can jump `settled` by thousands at once (baseline load) — that
// is not real throughput. Reject rate samples above this many records/sec so the
// ETA reflects actual indexing speed, not the baseline arriving. Real pipelines
// settle a few/sec; a baseline load is hundreds/sec.
const MAX_PLAUSIBLE_RATE = parseFloat(process.env.PROGRESS_MAX_RATE || '50');

const ACTIVE_ORGS_KEY = 'progress:active_orgs';
const HEARTBEAT_KEY = 'progress:indexer:heartbeat';
const connectorsKey = (org: string) => `progress:connectors:${org}`;
const countsKey = (org: string, conn: string) =>
  `progress:counts:${org}:${conn}`;
// Connector display name is a reserved field inside its counts hash (no separate
// key). Skipped by the status classifier so it never affects counts.
const NAME_FIELD = '__name';
const prevKey = (org: string) => `progress:prev:${org}`;

interface OrgTickState {
  etaRate: number | null; // EMA of settle rate (records/sec)
}

/**
 * Org-wide indexing progress ticker. Every tick it reads the `progress:*` counters
 * written by the Python services, computes one snapshot per active org, and emits
 * it to that org's admin socket room.
 *
 * The widget is a permanent "workspace indexing status" panel: counters are never
 * retired/deleted here — a completed workspace simply shows 100%. An org only
 * leaves the active set (and the widget hides) when it has zero records left.
 *
 * Best-effort throughout: a Redis error on one org must not stop the others or
 * crash the ticker.
 */
@injectable()
export class ProgressService {
  private readonly redis: Redis;
  private timer: NodeJS.Timeout | null = null;
  private readonly state = new Map<string, OrgTickState>();

  constructor(
    @inject('RedisService') private redisService: RedisService,
    @inject(NotificationService)
    private notificationService: NotificationService,
    @inject('Logger') private logger: Logger,
  ) {
    this.redis = this.redisService.getClient();
  }

  start(): void {
    if (this.timer) return;
    this.timer = setInterval(() => {
      this.tick().catch((err) => {
        this.logger.error('Progress tick failed', { error: err?.message });
      });
    }, TICK_MS);
    this.logger.info('Progress ticker started');
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  private async tick(): Promise<void> {
    const orgs = await this.redis.smembers(ACTIVE_ORGS_KEY);
    if (!orgs || orgs.length === 0) return;
    const now = Date.now();
    const alive = await this.isIndexerAlive(now);

    for (const orgId of orgs) {
      try {
        // persist=true: only the ticker advances ETA state.
        const snapshot = await this.computeSnapshot(orgId, now, alive, true);
        if (!snapshot) {
          // Org has zero records left (everything deleted) → drop it from the
          // active set and tell the widget to hide. This is the ONLY thing that
          // removes an org; a completed sync stays and shows 100%.
          await this.redis.srem(ACTIVE_ORGS_KEY, orgId);
          this.state.delete(orgId);
          this.notificationService.sendToRoom(`org:${orgId}:admins`, 'progressUpdate', {
            orgId,
            retired: true,
          });
          continue;
        }
        this.notificationService.sendToRoom(
          `org:${orgId}:admins`,
          'progressUpdate',
          snapshot,
        );
      } catch (err: any) {
        this.logger.warn('Progress snapshot failed for org', {
          orgId,
          error: err?.message,
        });
      }
    }
  }

  private async isIndexerAlive(now: number): Promise<boolean> {
    const hb = await this.redis.get(HEARTBEAT_KEY);
    if (!hb) return false;
    const ts = parseInt(hb, 10);
    return Number.isFinite(ts) && now - ts < HEARTBEAT_STALE_MS;
  }

  /**
   * Build the org snapshot from the per-connector count hashes. Returns null when
   * the org has nothing to show (no records and not in a completion window) so we
   * don't emit an empty bar.
   */
  async computeSnapshot(
    orgId: string,
    now: number = Date.now(),
    alive?: boolean,
    persist = false,
  ): Promise<ProgressSnapshot | null> {
    if (alive === undefined) alive = await this.isIndexerAlive(now);

    const connectors = await this.redis.smembers(connectorsKey(orgId));
    const rows: ConnectorProgress[] = [];
    let total = 0;
    let pending = 0;
    let done = 0;
    let failed = 0;
    let skipped = 0;

    if (connectors && connectors.length > 0) {
      const pipe = this.redis.pipeline();
      for (const conn of connectors) pipe.hgetall(countsKey(orgId, conn));
      const results = (await pipe.exec()) || [];

      for (let i = 0; i < connectors.length; i++) {
        const conn = connectors[i];
        if (conn === undefined) continue;
        const [, hash] = (results[i] || []) as [
          Error | null,
          Record<string, string>,
        ];
        const c = this.bucketConnector(conn, hash || {});
        if (c.total === 0) continue;
        rows.push(c);
        total += c.total;
        pending += c.pending;
        done += c.done;
        failed += c.failed;
        skipped += c.skipped;
      }
    }

    // Empty workspace (no records at all) → nothing to show.
    if (total === 0) return null;

    // Phase is purely a display label now — no retire attached to it.
    const phase: 'active' | 'complete' = pending === 0 ? 'complete' : 'active';

    const settled = done + failed + skipped;
    let percentage = total > 0 ? Math.round((settled / total) * 1000) / 10 : 0;
    // Never show 100% while records are still pending (rounding artifact) — cap
    // just below so "100%" strictly means done.
    if (pending > 0 && percentage >= 100) percentage = 99.9;
    const etaSeconds = await this.computeEta(
      orgId,
      pending,
      settled,
      now,
      alive,
      persist,
    );

    return {
      orgId,
      status: alive ? 'indexing' : 'paused',
      phase,
      total,
      pending,
      done,
      failed,
      skipped,
      percentage,
      etaSeconds,
      connectors: rows,
    };
  }

  private bucketConnector(
    connectorId: string,
    hash: Record<string, string>,
  ): ConnectorProgress {
    let pending = 0;
    let done = 0;
    let failed = 0;
    let skipped = 0;
    const connectorName = hash[NAME_FIELD];
    for (const [status, raw] of Object.entries(hash)) {
      if (status === NAME_FIELD) continue; // reserved name field, not a status
      const count = Math.max(0, parseInt(raw, 10) || 0); // clamp transient negatives
      switch (classify(status)) {
        case 'pending':
          pending += count;
          break;
        case 'done':
          done += count;
          break;
        case 'failed':
          failed += count;
          break;
        case 'skipped':
          skipped += count;
          break;
        default:
          break; // unclassified status: ignore (coverage test guards this)
      }
    }
    const total = pending + done + failed + skipped;
    const settled = done + failed + skipped;
    let percentage = total > 0 ? Math.round((settled / total) * 1000) / 10 : 0;
    if (pending > 0 && percentage >= 100) percentage = 99.9; // 100% only when done
    return {
      connectorId,
      // Real name from the seed's DB lookup; fall back to the id only if unseeded.
      connectorName: connectorName || connectorId,
      total,
      pending,
      done,
      failed,
      skipped,
      percentage,
    };
  }

  /**
   * Org-level ETA from the settle rate between ticks, smoothed with a light EMA.
   * Null when the indexer is paused, the rate is non-positive, or we're still
   * warming up (no usable prior sample).
   */
  private async computeEta(
    orgId: string,
    pending: number,
    settled: number,
    now: number,
    alive: boolean,
    persist: boolean,
  ): Promise<number | null> {
    // Complete → 0 so the ETA line always renders ("0s" reads as done).
    if (pending === 0) return 0;
    // Paused: no honest estimate while nothing is draining.
    if (!alive) return null;

    const st = this.getState(orgId);
    const prevRaw = await this.redis.get(prevKey(orgId));
    let prev: { settled: number; time: number } | null = null;
    if (prevRaw) {
      try {
        prev = JSON.parse(prevRaw);
      } catch {
        prev = null;
      }
    }

    if (!prev) {
      if (persist) {
        await this.redis.set(prevKey(orgId), JSON.stringify({ settled, time: now }));
      }
      return null; // warming up — need one baseline sample first
    }

    // Advance the baseline (and refresh the smoothed rate) only once the rolling
    // window has elapsed; between advances we reuse the EMA rate so the ETA stays
    // populated on every 5s tick instead of collapsing to 0.
    const dtSeconds = (now - prev.time) / 1000;
    // Stale baseline (e.g. Node restarted while this org's `prev` persisted in
    // Redis) — a multi-minute gap would yield a bogus rate, so reset and warm up.
    if (dtSeconds > 3 * ETA_WINDOW_SECONDS) {
      if (persist) {
        await this.redis.set(prevKey(orgId), JSON.stringify({ settled, time: now }));
      }
      return st.etaRate && st.etaRate > 0 ? Math.ceil(pending / st.etaRate) : null;
    }
    if (persist && dtSeconds >= ETA_WINDOW_SECONDS) {
      const instRate = (settled - prev.settled) / dtSeconds; // records/sec
      // Ignore baseline-load spikes; only fold in physically plausible rates.
      if (instRate > 0 && instRate <= MAX_PLAUSIBLE_RATE) {
        st.etaRate =
          st.etaRate === null ? instRate : st.etaRate * 0.6 + instRate * 0.4;
      }
      await this.redis.set(prevKey(orgId), JSON.stringify({ settled, time: now }));
    }

    if (!st.etaRate || st.etaRate <= 0) return null;
    return Math.ceil(pending / st.etaRate);
  }

  private getState(orgId: string): OrgTickState {
    let st = this.state.get(orgId);
    if (!st) {
      st = { etaRate: null };
      this.state.set(orgId, st);
    }
    return st;
  }

  /** True when the org currently has no counters in Redis (cold cache). */
  async isEmpty(orgId: string): Promise<boolean> {
    const isMember = await this.redis.sismember(ACTIVE_ORGS_KEY, orgId);
    return !isMember;
  }
}
