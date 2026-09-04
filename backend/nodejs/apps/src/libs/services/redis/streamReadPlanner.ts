/**
 * Groups streams by Redis hash slot so a single `XREADGROUP` call never
 * spans more than one slot (R1).
 *
 * On Redis Cluster / MemoryDB, `XREADGROUP STREAMS a b` raises CROSSSLOT
 * the moment `a` and `b` hash to different slots -- which lane streams
 * (`record-events.0` .. `.7`) and topic streams reliably do. On standalone,
 * `IRedisConnectionProvider.keySlot` always returns 0, so every stream
 * lands in one group and callers see exactly the single `XREADGROUP` call
 * they issued before this existed.
 *
 * Mirrors `backend/python/app/services/messaging/redis_streams/stream_read_planner.py`.
 */
import { IRedisConnectionProvider } from './connectionProvider.interface';

export class StreamReadPlanner {
  constructor(private readonly provider: IRedisConnectionProvider) {}

  /**
   * Return groups of stream names that each share one hash slot.
   *
   * Group order is stable (first-seen slot first) so round-robin polling
   * across groups is deterministic call to call; a stream added after the
   * planner was created is picked up the next time `group` runs, since
   * nothing here is cached.
   */
  group(streams: string[]): string[][] {
    if (streams.length === 0) {
      return [];
    }
    const bySlot = new Map<number, string[]>();
    for (const stream of streams) {
      const slot = this.provider.keySlot(stream);
      const existing = bySlot.get(slot);
      if (existing) {
        existing.push(stream);
      } else {
        bySlot.set(slot, [stream]);
      }
    }
    return Array.from(bySlot.values());
  }
}
