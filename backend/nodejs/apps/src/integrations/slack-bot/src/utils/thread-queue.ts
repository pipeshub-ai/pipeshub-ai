import { randomUUID } from 'crypto';
import { hostname } from 'os';
import type { SlackFile } from '../helpers';
import type {
  IRedisConnectionProvider,
  RedisClient,
} from '../../../../libs/services/redis/connectionProvider.interface';
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
    // Only enqueues are capped: a takeover adds its item to stranded ones, at most one over.
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

// KEYS[1] = lock, KEYS[2] = pending. Mirrors MemoryThreadQueueStore line by line.
// ARGV: owner, item JSON, maxPending, lockTtlMs, pendingTtlMs.
export const ACQUIRE_OR_ENQUEUE_LUA = `
if redis.call('SET', KEYS[1], ARGV[1], 'NX', 'PX', ARGV[4]) then
  redis.call('RPUSH', KEYS[2], ARGV[2])
  redis.call('PEXPIRE', KEYS[2], ARGV[5])
  return 1
end
if redis.call('LLEN', KEYS[2]) >= tonumber(ARGV[3]) then
  return -1
end
redis.call('RPUSH', KEYS[2], ARGV[2])
redis.call('PEXPIRE', KEYS[2], ARGV[5])
return 0
`;

// ARGV: owner, lockTtlMs.
export const TAKE_PENDING_OR_RELEASE_LUA = `
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
  return {}
end
if redis.call('LLEN', KEYS[2]) == 0 then
  redis.call('DEL', KEYS[1])
  return {}
end
local items = redis.call('LRANGE', KEYS[2], 0, -1)
redis.call('DEL', KEYS[2])
redis.call('PEXPIRE', KEYS[1], ARGV[2])
return items
`;

// ARGV: owner, lockTtlMs.
export const RENEW_LUA = `
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('PEXPIRE', KEYS[1], ARGV[2])
  return 1
end
return 0
`;

type ScriptName = 'acquire' | 'take' | 'renew';
const SCRIPT_BODIES: Record<ScriptName, string> = {
  acquire: ACQUIRE_OR_ENQUEUE_LUA,
  take: TAKE_PENDING_OR_RELEASE_LUA,
  renew: RENEW_LUA,
};

export class RedisThreadQueueStore implements ThreadQueueStore {
  private readonly shas: Record<ScriptName, string>;
  private readonly lockTtlMs: number;
  private readonly pendingTtlMs: number;
  private readonly loadScript: (body: string) => Promise<string>;

  constructor(
    private readonly client: RedisClient,
    scriptShas: Record<ScriptName, string>,
    options: {
      lockTtlMs?: number;
      pendingTtlMs?: number;
      loadScript?: (body: string) => Promise<string>;
    } = {},
  ) {
    this.shas = { ...scriptShas };
    this.lockTtlMs = options.lockTtlMs ?? 120_000;
    this.pendingTtlMs = options.pendingTtlMs ?? 1_800_000;
    // The provider loads on every cluster master; a plain SCRIPT LOAD may land on another node.
    this.loadScript =
      options.loadScript ??
      ((body) => client.script('LOAD', body) as Promise<string>);
  }

  private async run(
    name: ScriptName,
    key: string,
    ...args: Array<string | number>
  ): Promise<unknown> {
    const call = (): Promise<unknown> =>
      this.client.evalsha(
        this.shas[name],
        2,
        `${key}:lock`,
        `${key}:pending`,
        ...args,
      );
    try {
      return await call();
    } catch (err) {
      if (!(err instanceof Error && err.message.includes('NOSCRIPT')))
        throw err;
      // SCRIPT FLUSH or a node replacement dropped the cache: reload once and retry.
      this.shas[name] = await this.loadScript(SCRIPT_BODIES[name]);
      return call();
    }
  }

  async acquireOrEnqueue(
    key: string,
    owner: string,
    item: QueuedSlackMessage,
    maxPending: number,
  ): Promise<'acquired' | 'queued' | 'full'> {
    const r = await this.run(
      'acquire',
      key,
      owner,
      JSON.stringify(item),
      maxPending,
      this.lockTtlMs,
      this.pendingTtlMs,
    );
    return r === 1 ? 'acquired' : r === 0 ? 'queued' : 'full';
  }

  async takePendingOrRelease(
    key: string,
    owner: string,
  ): Promise<QueuedSlackMessage[]> {
    const items = (await this.run(
      'take',
      key,
      owner,
      this.lockTtlMs,
    )) as string[];
    // The script already removed these, so one corrupt item must not cost the rest.
    const batch: QueuedSlackMessage[] = [];
    for (const raw of items) {
      try {
        const m: unknown = JSON.parse(raw);
        if (
          typeof m === 'object' &&
          m !== null &&
          typeof (m as { ts?: unknown }).ts === 'string'
        ) {
          batch.push(m as QueuedSlackMessage);
          continue;
        }
      } catch {
        // Logged below with the other unusable shapes.
      }
      console.warn('[slack-bot] thread queue skipped a corrupt item');
    }
    // [] would end the caller's loop with the lock still held; take again to release it.
    if (batch.length === 0 && items.length > 0) {
      return this.takePendingOrRelease(key, owner);
    }
    return batch;
  }

  async renew(key: string, owner: string): Promise<boolean> {
    return (await this.run('renew', key, owner, this.lockTtlMs)) === 1;
  }
}

export async function createThreadQueueStore(
  provider: IRedisConnectionProvider | null,
): Promise<{ store: ThreadQueueStore; namespace: string; shared: boolean }> {
  try {
    if (provider && (await provider.ping())) {
      const load = (body: string): Promise<string> => provider.loadScript(body);
      const store = new RedisThreadQueueStore(
        provider.getClient(),
        {
          acquire: await load(ACQUIRE_OR_ENQUEUE_LUA),
          take: await load(TAKE_PENDING_OR_RELEASE_LUA),
          renew: await load(RENEW_LUA),
        },
        { loadScript: load },
      );
      return { store, namespace: provider.keyNamespace, shared: true };
    }
  } catch {
    // Fall through: an unreachable Redis means per-process queueing, not no bot.
  }
  console.warn(
    'Slack thread queue: Redis unavailable, queueing is per process',
  );
  return { store: new MemoryThreadQueueStore(), namespace: '', shared: false };
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
    onBusy?: (batch: QueuedSlackMessage[]) => void | Promise<void>,
  ): Promise<SubmitResult> {
    const runBatch = async (
      batch: QueuedSlackMessage[],
    ): Promise<'ran' | 'busy'> => {
      try {
        const r = await this.limiter.run(() => run(batch));
        if (r.status === 'ran') return 'ran';
      } catch (err) {
        console.error('[slack-bot] answer failed', err);
        return 'ran';
      }
      // The batch has left the store, so this is the only chance to tell its senders.
      try {
        await onBusy?.(batch);
      } catch (err) {
        console.error('[slack-bot] busy notice failed', err);
      }
      return 'busy';
    };
    const runDirect = async (): Promise<SubmitResult> =>
      (await runBatch([item])) === 'busy' ? 'busy' : 'direct';

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
      return runDirect();
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
    let storeFailedFirst = false;
    try {
      for (;;) {
        const batch = await this.store.takePendingOrRelease(key, this.owner);
        if (batch.length === 0) break;
        if ((await runBatch(batch)) === 'busy' && first) result = 'busy';
        first = false;
        // Another owner took over after we stalled past the TTL; it drains.
        if (lock.lost) break;
      }
    } catch (err) {
      // Later pending items are picked up by the thread's next message or expire.
      console.warn('[slack-bot] thread queue hand-off failed', err);
      storeFailedFirst = first;
    } finally {
      clearInterval(timer);
    }
    // Our item never left the store, so answer it here rather than lose it.
    return storeFailedFirst ? runDirect() : result;
  }
}
