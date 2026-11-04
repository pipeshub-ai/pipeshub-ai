import { randomUUID } from 'crypto';
import { hostname } from 'os';
import type { SlackFile } from '../helpers';
import type { RunLimiter } from './run-limiter';

export interface QueuedSlackMessage {
  kind: 'dm' | 'mention';
  ts: string;
  threadTs: string;
  channel: string;
  user: string;
  text: string;
  files: SlackFile[];
  receivedAt: number;
}

export interface ThreadQueueStore {
  /**
   * Take the lock (run now) or append to pending (queued). One atomic step.
   * On 'acquired' the item is appended to pending too, so the owner's first
   * takePendingOrRelease returns any items stranded by an expired owner followed by it.
   */
  acquireOrEnqueue(
    key: string,
    owner: string,
    item: QueuedSlackMessage,
    maxPending: number,
  ): Promise<'acquired' | 'queued' | 'full'>;
  /** Owner only: take every pending item, or release the lock when none. One atomic step. [] = released or lost. */
  takePendingOrRelease(
    key: string,
    owner: string,
  ): Promise<QueuedSlackMessage[]>;
  /** Owner only: extend the lock. False when the lock is no longer ours. */
  renew(key: string, owner: string): Promise<boolean>;
}

export function threadQueueKey(
  parts: { botId: string; channel: string; threadTs: string; user: string },
  namespace = '',
): string {
  // The braces are a Redis Cluster hash tag: the lock and pending keys share a slot.
  return `${namespace}slackq:{${parts.botId}:${parts.channel}:${parts.threadTs}:${parts.user}}`;
}

interface MemoryEntry {
  owner: string | null;
  lockExpiresAt: number;
  pending: QueuedSlackMessage[];
  pendingExpiresAt: number;
}

export class MemoryThreadQueueStore implements ThreadQueueStore {
  private readonly entries = new Map<string, MemoryEntry>();
  private readonly lockTtlMs: number;
  private readonly pendingTtlMs: number;

  constructor(
    private readonly now: () => number = Date.now,
    options: { lockTtlMs?: number; pendingTtlMs?: number } = {},
  ) {
    this.lockTtlMs = options.lockTtlMs ?? 120_000;
    this.pendingTtlMs = options.pendingTtlMs ?? 1_800_000;
  }

  get size(): number {
    return this.entries.size;
  }

  /** Applies expiry lazily, as Redis TTLs would; drops the entry when nothing is left. */
  private live(key: string): MemoryEntry | undefined {
    const e = this.entries.get(key);
    if (!e) return undefined;
    const t = this.now();
    if (e.owner !== null && t >= e.lockExpiresAt) e.owner = null;
    if (e.pending.length > 0 && t >= e.pendingExpiresAt) e.pending = [];
    if (e.owner === null && e.pending.length === 0) {
      this.entries.delete(key);
      return undefined;
    }
    return e;
  }

  // Each operation is synchronous, so it is atomic within the process like a Redis script.
  acquireOrEnqueue(
    key: string,
    owner: string,
    item: QueuedSlackMessage,
    maxPending: number,
  ): Promise<'acquired' | 'queued' | 'full'> {
    const t = this.now();
    let e = this.live(key);
    if (!e) {
      e = { owner: null, lockExpiresAt: 0, pending: [], pendingExpiresAt: 0 };
      this.entries.set(key, e);
    }
    const acquired = e.owner === null;
    if (acquired) {
      e.owner = owner;
      e.lockExpiresAt = t + this.lockTtlMs;
    } else if (e.pending.length >= maxPending) {
      return Promise.resolve('full');
    }
    e.pending.push(item);
    e.pendingExpiresAt = t + this.pendingTtlMs;
    return Promise.resolve(acquired ? 'acquired' : 'queued');
  }

  takePendingOrRelease(
    key: string,
    owner: string,
  ): Promise<QueuedSlackMessage[]> {
    const e = this.live(key);
    if (e?.owner !== owner) return Promise.resolve([]);
    if (e.pending.length === 0) {
      this.entries.delete(key);
      return Promise.resolve([]);
    }
    const batch = e.pending;
    e.pending = [];
    e.lockExpiresAt = this.now() + this.lockTtlMs;
    return Promise.resolve(batch);
  }

  renew(key: string, owner: string): Promise<boolean> {
    const e = this.live(key);
    if (e?.owner !== owner) return Promise.resolve(false);
    e.lockExpiresAt = this.now() + this.lockTtlMs;
    return Promise.resolve(true);
  }
}

export type SubmitResult = 'ran' | 'queued' | 'full' | 'busy' | 'direct';

export class ThreadQueue {
  private readonly maxPending: number;
  private readonly renewMs: number;
  private readonly owner: string;

  constructor(
    private readonly store: ThreadQueueStore,
    private readonly limiter: RunLimiter,
    options: { maxPending?: number; renewMs?: number; owner?: string } = {},
  ) {
    this.maxPending = options.maxPending ?? 10;
    this.renewMs = options.renewMs ?? 30_000;
    this.owner = options.owner ?? `${hostname()}:${randomUUID()}`;
  }

  async submit(
    key: string,
    item: QueuedSlackMessage,
    run: (batch: QueuedSlackMessage[]) => Promise<void>,
  ): Promise<SubmitResult> {
    let outcome: 'acquired' | 'queued' | 'full';
    try {
      outcome = await this.store.acquireOrEnqueue(
        key,
        this.owner,
        item,
        this.maxPending,
      );
    } catch (err) {
      console.warn(
        '[slack-bot] thread queue unavailable, answering without queueing',
        err,
      );
      try {
        const r = await this.limiter.run(() => run([item]));
        return r.status === 'busy' ? 'busy' : 'direct';
      } catch (runErr) {
        console.error('[slack-bot] answer failed', runErr);
        return 'direct';
      }
    }
    if (outcome !== 'acquired') return outcome;

    const lock = { lost: false };
    const timer = setInterval(() => {
      this.store.renew(key, this.owner).then(
        (ok) => {
          if (!ok) lock.lost = true;
        },
        (err: unknown) => {
          console.warn('[slack-bot] thread queue renew failed', err);
        },
      );
    }, this.renewMs);
    timer.unref();

    let result: SubmitResult = 'ran';
    let first = true;
    try {
      for (;;) {
        const batch = await this.store.takePendingOrRelease(key, this.owner);
        if (batch.length === 0) break;
        try {
          const r = await this.limiter.run(() => run(batch));
          if (first && r.status === 'busy') result = 'busy';
        } catch (err) {
          console.error('[slack-bot] answer failed', err);
        }
        first = false;
        // Another owner took over after we stalled past the TTL; it drains.
        if (lock.lost) break;
      }
    } catch (err) {
      // Pending items are picked up by the thread's next message or expire.
      console.warn('[slack-bot] thread queue hand-off failed', err);
    } finally {
      clearInterval(timer);
    }
    return result;
  }
}
