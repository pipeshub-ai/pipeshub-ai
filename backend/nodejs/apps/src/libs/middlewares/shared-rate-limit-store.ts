import { MemoryStore, Options, Store, ClientRateLimitInfo } from 'express-rate-limit';
import { ICacheService } from '../services/cache/cacheService.interface';
import { Logger } from '../services/logger.service';
import { recordRateLimitStoreFallback } from '../services/telemetry/modules/rate-limit-store-metrics';

let sharedCache: ICacheService | undefined;

/** Set once at boot. Limiters read it per request, so they may be built before it is set. */
export function useRateLimitCache(cache: ICacheService | undefined): void {
  sharedCache = cache;
}

/** A hung Redis must not hold every request; past this the hit is counted in-process. */
export const RATE_LIMIT_STORE_TIMEOUT_MS = 250;
const WARN_INTERVAL_MS = 30_000;
/** After a failure the store is skipped this long, so an outage costs one timeout per limiter, not one per request. */
export const RATE_LIMIT_STORE_RETRY_MS = 5_000;

function withTimeout<T>(work: Promise<T>, ms: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout>;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`rate-limit store timed out after ${String(ms)} ms`)), ms);
  });
  return Promise.race([work, timeout]).finally(() => clearTimeout(timer));
}

/**
 * Fixed-window counter shared by every replica through `ICacheService`. The window index is part of the
 * key, so a key expires on its own and a crash between INCR and EXPIRE leaves at most one stale key.
 * A client can burst up to 2x the limit across a window boundary. Redis errors (and a missing cache)
 * fall back to an in-process `MemoryStore` for `RATE_LIMIT_STORE_RETRY_MS`: the limit then holds per replica
 * instead of globally, and the API never fails because of the limiter. A cache call that times out but lands
 * later is counted in both places, which only errs towards limiting.
 */
export class SharedRateLimitStore implements Store {
  readonly localKeys = false;
  private windowMs = 60_000;
  private readonly fallback = new MemoryStore();
  private lastWarnAt = 0;
  private skipSharedUntil = 0;

  constructor(
    private readonly limiterName: string,
    private readonly logger: Pick<Logger, 'warn'>,
    private readonly now: () => number = Date.now,
    private readonly timeoutMs = RATE_LIMIT_STORE_TIMEOUT_MS,
  ) {}

  init(options: Options): void {
    this.windowMs = options.windowMs;
    this.fallback.init(options);
  }

  private windowStart(): number {
    return Math.floor(this.now() / this.windowMs) * this.windowMs;
  }

  private keyFor(key: string, windowStart: number): string {
    return `ratelimit:${key}:${String(windowStart)}`;
  }

  async increment(key: string): Promise<ClientRateLimitInfo> {
    const cache = sharedCache;
    if (!cache) return this.fallback.increment(key);
    if (this.now() < this.skipSharedUntil) {
      recordRateLimitStoreFallback(this.limiterName);
      return this.fallback.increment(key);
    }
    const start = this.windowStart();
    try {
      const totalHits = await withTimeout(
        cache.increment(this.keyFor(key, start), { ttl: Math.ceil(this.windowMs / 1000) + 1 }),
        this.timeoutMs,
      );
      return { totalHits, resetTime: new Date(start + this.windowMs) };
    } catch (error) {
      this.noteFailure(error);
      return this.fallback.increment(key);
    }
  }

  // A fixed window cannot give a hit back; no keyed limiter uses skip*Requests.
  async decrement(key: string): Promise<void> {
    await this.fallback.decrement(key);
  }

  async resetKey(key: string): Promise<void> {
    await this.fallback.resetKey(key);
    const cache = sharedCache;
    if (!cache) return;
    try {
      await withTimeout(cache.delete(this.keyFor(key, this.windowStart())), this.timeoutMs);
    } catch (error) {
      this.noteFailure(error);
    }
  }

  shutdown(): void {
    this.fallback.shutdown();
  }

  private noteFailure(error: unknown): void {
    recordRateLimitStoreFallback(this.limiterName);
    const now = this.now();
    this.skipSharedUntil = now + RATE_LIMIT_STORE_RETRY_MS;
    if (now - this.lastWarnAt < WARN_INTERVAL_MS) return;
    this.lastWarnAt = now;
    this.logger.warn('Shared rate-limit store failed; counting in-process', {
      limiter: this.limiterName,
      error: error instanceof Error ? error.message : String(error),
    });
  }
}
