import { HarnessUnavailableError } from '../domain/errors';
import { ConfigDocumentStore } from '../ports/config-document-store';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';

export const CONFIG_CAS_ATTEMPTS = 5;

/**
 * Applies `change` under compare-and-set and returns the stored value.
 * `change` answers null to keep the current value; a lost race re-reads, so
 * replicas that start together converge on the first writer's value.
 */
export async function updateConfigDocument<T>(
  store: ConfigDocumentStore<T>,
  change: (current: T | null) => T | null,
): Promise<T> {
  for (let attempt = 0; attempt < CONFIG_CAS_ATTEMPTS; attempt += 1) {
    const { value, revision } = await store.read();
    const next = change(value);
    if (next === null) {
      if (value === null) {
        throw new Error('A config document change must create a missing value');
      }
      return value;
    }
    if (await store.replace(revision, next)) return next;
  }
  throw new HarnessUnavailableError(
    'Agent harness configuration changed concurrently',
  );
}

export interface StaleServing {
  /** Names the document in logs. */
  name: string;
  /** How long past its TTL a copy may serve while the store cannot be read. */
  maxMs: number;
  logger?: HarnessLogger;
}

/**
 * Caches one config document per replica with a TTL and rate-limited forced
 * reloads. With `stale`, a TTL reload that fails (the KV store is down) keeps
 * serving the last good copy for a bounded time instead of failing every
 * caller; a forced `refresh` never serves stale. Once a reload has failed,
 * later callers get that copy at once while one reload at a time runs behind
 * it: a down store takes its client's whole retry budget to fail (ioredis:
 * seconds), and each caller paying it again stalls every claim and renewal
 * through the outage (QA17-F3). A healthy store is always read through, so a
 * rotation is never answered from a copy.
 */
export class CachedConfigDocument<T> {
  private cached: { value: T | null; loadedAt: number } | null = null;
  private servingStale = false;
  private revalidating: Promise<void> | null = null;

  constructor(
    private readonly store: ConfigDocumentStore<T>,
    private readonly now: () => number,
    private readonly ttlMs: number,
    private readonly minReloadMs: number,
    private readonly stale?: StaleServing,
  ) {}

  async get(): Promise<T | null> {
    const cached = this.cached;
    if (cached !== null && this.now() - cached.loadedAt < this.ttlMs) {
      return cached.value;
    }
    if (
      cached !== null &&
      this.servingStale &&
      this.withinStaleBound(cached.loadedAt)
    ) {
      this.revalidate();
      return cached.value;
    }
    try {
      return await this.reload();
    } catch (error) {
      if (cached === null || !this.withinStaleBound(cached.loadedAt))
        throw error;
      this.servedStale(error, cached.loadedAt);
      return cached.value;
    }
  }

  private withinStaleBound(loadedAt: number): boolean {
    return (
      this.stale !== undefined &&
      this.now() - loadedAt < this.ttlMs + this.stale.maxMs
    );
  }

  /** The reload behind a stale copy: one at a time; its failure keeps the copy serving. */
  private revalidate(): void {
    if (this.revalidating !== null) return;
    this.revalidating = this.reload()
      .then(
        () => undefined,
        () => undefined,
      )
      .finally(() => {
        this.revalidating = null;
      });
  }

  private servedStale(error: unknown, loadedAt: number): void {
    if (this.servingStale || this.stale === undefined) return;
    this.servingStale = true;
    (this.stale.logger ?? silentHarnessLogger).warn(
      'authority.config_stale',
      harnessLogMeta('authority.config_stale', {
        document: this.stale.name,
        ageMs: this.now() - loadedAt,
        maxStaleMs: this.stale.maxMs,
        errorType: error instanceof Error ? error.name : typeof error,
      }),
    );
  }

  /** Re-reads unless the last read is newer than the minimum reload interval; null if skipped. */
  async refresh(): Promise<{ value: T | null } | null> {
    if (
      this.cached !== null &&
      this.now() - this.cached.loadedAt < this.minReloadMs
    ) {
      return null;
    }
    return { value: await this.reload() };
  }

  remember(value: T): void {
    this.cached = { value, loadedAt: this.now() };
  }

  private async reload(): Promise<T | null> {
    const { value } = await this.store.read();
    this.cached = { value, loadedAt: this.now() };
    if (this.servingStale && this.stale !== undefined) {
      this.servingStale = false;
      (this.stale.logger ?? silentHarnessLogger).info(
        'authority.config_recovered',
        harnessLogMeta('authority.config_recovered', {
          document: this.stale.name,
        }),
      );
    }
    return value;
  }
}
