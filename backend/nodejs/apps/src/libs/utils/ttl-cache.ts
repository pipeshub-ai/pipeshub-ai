import { IClock, systemClock } from '../types/clock';

/** In-process cache with one TTL for every entry. Past `maxEntries` it drops expired entries first, then everything. */
export class TtlCache<V> {
  private readonly entries = new Map<string, { value: V; expiresAt: number }>();

  constructor(
    private readonly ttlMs: number,
    private readonly maxEntries: number,
    private readonly clock: IClock = systemClock,
  ) {}

  get(key: string): V | undefined {
    const hit = this.entries.get(key);
    return hit && hit.expiresAt > this.clock.now() ? hit.value : undefined;
  }

  set(key: string, value: V): void {
    const now = this.clock.now();
    if (this.entries.size >= this.maxEntries && !this.entries.has(key)) {
      for (const [k, v] of this.entries) {
        if (v.expiresAt <= now) {
          this.entries.delete(k);
        }
      }
      if (this.entries.size >= this.maxEntries) {
        this.entries.clear();
      }
    }
    this.entries.set(key, { value, expiresAt: now + this.ttlMs });
  }

  delete(key: string): void {
    this.entries.delete(key);
  }
}
