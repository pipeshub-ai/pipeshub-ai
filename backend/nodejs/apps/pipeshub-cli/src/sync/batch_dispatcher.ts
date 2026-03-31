import type { FileEvent } from "./watcher_state";

/**
 * Batches correlated FileEvents and dispatches them to a callback
 * with configurable flush interval and max batch size.
 *
 * Handles deduplication within a batch (e.g. CREATED+MODIFIED → CREATED,
 * CREATED+DELETED → drop both, MODIFIED+DELETED → DELETED only).
 */

export type BatchDispatchMeta = {
  batchId: string;
  source: "live" | "reconcile";
};

export type DispatchFn = (
  events: FileEvent[],
  meta: BatchDispatchMeta
) => Promise<void>;

export interface BatchDispatcherOptions {
  /** Max events before auto-flush. Default 50. */
  maxBatchSize?: number;
  /** Max ms between flushes. Default 1000. */
  flushIntervalMs?: number;
  /** Called on dispatch error. */
  onError?: (err: unknown) => void;
}

export class BatchDispatcher {
  private readonly maxBatch: number;
  private readonly flushIntervalMs: number;
  private readonly dispatch: DispatchFn;
  private readonly onError: (err: unknown) => void;

  private buffer: FileEvent[] = [];
  private batchSource: "live" | "reconcile" = "live";
  private flushTimer: NodeJS.Timeout | null = null;
  private flushing = false;
  private paused = false;

  constructor(dispatchFn: DispatchFn, opts?: BatchDispatcherOptions) {
    this.dispatch = dispatchFn;
    this.maxBatch = opts?.maxBatchSize ?? 50;
    this.flushIntervalMs = opts?.flushIntervalMs ?? 1000;
    this.onError = opts?.onError ?? ((err) => console.error("[BatchDispatcher] error:", err));
  }

  /** Add events to the buffer. Flushes automatically when full or on timer. */
  push(
    events: FileEvent[],
    opts?: { source?: "live" | "reconcile" }
  ): void {
    if (events.length === 0) return;
    if (opts?.source) {
      this.batchSource = opts.source;
    }
    this.buffer.push(...events);
    if (this.buffer.length >= this.maxBatch) {
      void this.flush();
    } else {
      this.scheduleFlush();
    }
  }

  pause(): void {
    this.paused = true;
  }

  resume(): void {
    this.paused = false;
    if (this.buffer.length > 0) {
      this.scheduleFlush();
    }
  }

  private scheduleFlush(): void {
    if (this.flushTimer) return;
    this.flushTimer = setTimeout(() => {
      this.flushTimer = null;
      void this.flush();
    }, this.flushIntervalMs);
  }

  /** Flush buffered events to the dispatch function. */
  async flush(): Promise<void> {
    if (this.flushTimer) {
      clearTimeout(this.flushTimer);
      this.flushTimer = null;
    }
    if (this.buffer.length === 0 || this.flushing || this.paused) return;

    this.flushing = true;
    const batch = this.deduplicate(this.buffer.splice(0));
    if (batch.length === 0) {
      this.flushing = false;
      return;
    }

    const source = this.batchSource;
    this.batchSource = "live";
    const batchId = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;

    try {
      await this.dispatch(batch, { batchId, source });
    } catch (err) {
      this.onError(err);
    } finally {
      this.flushing = false;
      // If more events accumulated during dispatch, schedule another flush
      if (this.buffer.length > 0 && !this.paused) {
        this.scheduleFlush();
      }
    }
  }

  /**
   * Deduplicate events within a batch:
   * - CREATED + MODIFIED on same path → keep CREATED (with latest size)
   * - CREATED + DELETED on same path → drop both
   * - MODIFIED + DELETED on same path → keep DELETED
   * - RENAMED/MOVED + MODIFIED on same path → keep RENAMED/MOVED
   * - Multiple MODIFIED on same path → keep last
   */
  private deduplicate(events: FileEvent[]): FileEvent[] {
    const byPath = new Map<string, FileEvent[]>();
    for (const ev of events) {
      const key = ev.path;
      const arr = byPath.get(key) ?? [];
      arr.push(ev);
      byPath.set(key, arr);
    }

    const result: FileEvent[] = [];
    for (const [, pathEvents] of byPath) {
      if (pathEvents.length === 1) {
        result.push(pathEvents[0]!);
        continue;
      }

      const types = new Set(pathEvents.map((e) => e.type));
      const last = pathEvents[pathEvents.length - 1]!;

      // CREATED then DELETED → drop both
      if (types.has("CREATED") && types.has("DELETED")) {
        continue;
      }
      if (types.has("DIR_CREATED") && types.has("DIR_DELETED")) {
        continue;
      }

      // CREATED + MODIFIED → CREATED with latest metadata
      if (types.has("CREATED") && types.has("MODIFIED")) {
        const created = pathEvents.find((e) => e.type === "CREATED")!;
        result.push({ ...created, size: last.size, timestamp: last.timestamp });
        continue;
      }

      // MODIFIED + DELETED → DELETED
      if (types.has("MODIFIED") && types.has("DELETED")) {
        result.push(pathEvents.find((e) => e.type === "DELETED")!);
        continue;
      }

      // RENAMED/MOVED + MODIFIED → keep RENAMED/MOVED
      const renameOrMove = pathEvents.find(
        (e) => e.type === "RENAMED" || e.type === "MOVED" ||
               e.type === "DIR_RENAMED" || e.type === "DIR_MOVED"
      );
      if (renameOrMove && types.has("MODIFIED")) {
        result.push(renameOrMove);
        continue;
      }

      // Multiple MODIFIED → keep last
      if (pathEvents.every((e) => e.type === "MODIFIED")) {
        result.push(last);
        continue;
      }

      // Fallback: keep the last event
      result.push(last);
    }

    return result;
  }

  get pending(): number {
    return this.buffer.length;
  }
}
