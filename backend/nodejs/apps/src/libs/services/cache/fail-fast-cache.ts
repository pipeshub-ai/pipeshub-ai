import { CacheOptions } from '../../types/redis.types';
import { Logger } from '../logger.service';
import { ICacheService } from './cacheService.interface';

/** A hung cache must not hold the request; past this the call counts as failed. */
export const FAIL_FAST_CACHE_TIMEOUT_MS = 250;
/** After a failure the cache is skipped this long, so an outage costs one timeout, not one per call. */
export const FAIL_FAST_CACHE_RETRY_MS = 5_000;
const WARN_INTERVAL_MS = 30_000;

export function withTimeout<T>(work: Promise<T>, ms: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout>;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      reject(new Error(`cache call timed out after ${String(ms)} ms`));
    }, ms);
  });
  return Promise.race([work, timeout]).finally(() => {
    clearTimeout(timer);
  });
}

/**
 * A cache that fails open, and fast. Every call races `timeoutMs`, and after a failure the inner cache
 * is skipped for `FAIL_FAST_CACHE_RETRY_MS`, then until the one call let through finds it answering again.
 * A failed or skipped read is a miss and a failed or skipped write is dropped; `increment` has no safe
 * stand-in value, so it rejects. Callers sharing one instance share the skip window.
 */
export class FailFastCache implements ICacheService {
  private skipUntil = 0;
  private probing = false;
  private lastWarnAt = Number.NEGATIVE_INFINITY;

  constructor(
    private readonly inner: ICacheService,
    private readonly logger: Pick<Logger, 'warn'>,
    private readonly now: () => number = Date.now,
    private readonly timeoutMs = FAIL_FAST_CACHE_TIMEOUT_MS,
  ) {}

  get<T>(key: string, options?: CacheOptions): Promise<T | null> {
    return this.run(() => this.inner.get<T>(key, options)).catch(() => null);
  }

  set(key: string, value: unknown, options?: CacheOptions): Promise<void> {
    return this.run(() => this.inner.set(key, value, options)).catch(
      () => undefined,
    );
  }

  delete(key: string, options?: CacheOptions): Promise<void> {
    return this.run(() => this.inner.delete(key, options)).catch(
      () => undefined,
    );
  }

  increment(key: string, options?: CacheOptions): Promise<number> {
    return this.run(() => this.inner.increment(key, options));
  }

  disconnect(): Promise<void> {
    return this.inner.disconnect();
  }

  /** False while the inner cache is being skipped. */
  isConnected(): boolean {
    return (
      this.now() >= this.skipUntil && !this.probing && this.inner.isConnected()
    );
  }

  private async run<T>(call: () => Promise<T>): Promise<T> {
    if (this.now() < this.skipUntil || this.probing) {
      throw new Error('cache skipped after a recent failure');
    }
    // After a failure one call tries the inner cache again, so an outage costs one timeout per window, not one per caller.
    const probe = this.skipUntil !== 0;
    this.probing = probe;
    try {
      const value = await withTimeout(call(), this.timeoutMs);
      if (probe) this.skipUntil = 0;
      return value;
    } catch (error) {
      this.noteFailure(error);
      throw error;
    } finally {
      if (probe) this.probing = false;
    }
  }

  private noteFailure(error: unknown): void {
    const now = this.now();
    this.skipUntil = now + FAIL_FAST_CACHE_RETRY_MS;
    if (now - this.lastWarnAt < WARN_INTERVAL_MS) return;
    this.lastWarnAt = now;
    this.logger.warn('Cache failed; skipping it for a few seconds', {
      error: error instanceof Error ? error.message : String(error),
    });
  }
}
