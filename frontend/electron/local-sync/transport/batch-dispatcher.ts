import type { WatchEvent } from '../watcher/replay-event-expander';

export interface BatchDispatcherOptions {
  maxBatchSize?: number;
  flushIntervalMs?: number;
  onError?: (err: unknown) => void;
}

export interface BatchPushOptions {
  source?: string;
}

export interface BatchMeta {
  batchId: string;
  source: string;
}

export type BatchDispatchFn = (events: WatchEvent[], meta: BatchMeta) => Promise<unknown> | unknown;

interface BufferedChunk {
  events: WatchEvent[];
  source: string;
}

/**
 * In-memory event buffer with timer-based flush. Coalesces watcher event
 * spurts into batches no larger than `maxBatchSize`, dedupes redundant
 * per-path events (CREATED+MODIFIED, MODIFIED+DELETED, ...) inside a flush,
 * and serializes the underlying `dispatch` callback so a slow consumer
 * doesn't get re-entered.
 */
export class BatchDispatcher {
  private dispatch: BatchDispatchFn;
  private maxBatch: number;
  private flushIntervalMs: number;
  private onError: (err: unknown) => void;
  private buffer: BufferedChunk[];
  private flushTimer: NodeJS.Timeout | null;
  private flushing: boolean;
  private paused: boolean;
  private flushPromise: Promise<void> | null;

  constructor(dispatchFn: BatchDispatchFn, opts: BatchDispatcherOptions = {}) {
    this.dispatch = dispatchFn;
    this.maxBatch = opts.maxBatchSize != null ? opts.maxBatchSize : 50;
    this.flushIntervalMs = opts.flushIntervalMs != null ? opts.flushIntervalMs : 1000;
    this.onError = opts.onError || ((err: unknown) => console.error('[BatchDispatcher] error:', err));
    this.buffer = [];
    this.flushTimer = null;
    this.flushing = false;
    this.paused = false;
    this.flushPromise = null;
  }

  push(events: WatchEvent[], opts?: BatchPushOptions): void {
    if (!events || events.length === 0) return;
    const source = (opts && opts.source) || 'live';
    this.buffer.push({ events: [...events], source });
    if (this.pending >= this.maxBatch) {
      this.flush();
    } else {
      this.scheduleFlush();
    }
  }

  get pending(): number {
    return this.buffer.reduce((n, c) => n + c.events.length, 0);
  }

  pause(): void { this.paused = true; }
  resume(): void {
    this.paused = false;
    if (this.pending > 0) this.scheduleFlush();
  }

  private scheduleFlush(): void {
    if (this.flushTimer) return;
    this.flushTimer = setTimeout(() => {
      this.flushTimer = null;
      this.flush();
    }, this.flushIntervalMs);
  }

  flush(): Promise<void> {
    // Coalesce concurrent flush calls onto the same in-flight promise. Without
    // this, watcher.stop() awaits a no-op while a different flush is mid-drain
    // — stop() returns before all events are journaled.
    if (this.flushPromise) return this.flushPromise;
    if (this.flushTimer) { clearTimeout(this.flushTimer); this.flushTimer = null; }
    if (this.buffer.length === 0 || this.paused) return Promise.resolve();
    this.flushPromise = this._flushInner().finally(() => {
      this.flushPromise = null;
    });
    return this.flushPromise;
  }

  private async _flushInner(): Promise<void> {
    this.flushing = true;
    const chunks = this.buffer.splice(0);
    try {
      for (const chunk of chunks) {
        const batch = this.deduplicate(chunk.events);
        if (batch.length === 0) continue;
        const batchId = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
        await this.dispatch(batch, { batchId, source: chunk.source });
      }
    } catch (err) {
      this.onError(err);
    } finally {
      this.flushing = false;
      if (this.buffer.length > 0 && !this.paused) this.scheduleFlush();
    }
  }

  private deduplicate(events: WatchEvent[]): WatchEvent[] {
    const byPath = new Map<string, WatchEvent[]>();
    for (const ev of events) {
      const arr = byPath.get(ev.path) || [];
      arr.push(ev);
      byPath.set(ev.path, arr);
    }
    const result: WatchEvent[] = [];
    for (const [, pathEvents] of byPath) {
      if (pathEvents.length === 1) { result.push(pathEvents[0]); continue; }
      const types = new Set(pathEvents.map((e) => e.type));
      const last = pathEvents[pathEvents.length - 1];
      // CREATED + DELETED in one batch: emit final state. If DELETED is last,
      // the file is gone — emit DELETED so backend cleans up. If CREATED is
      // last (rare: deleted then re-created), emit CREATED. Dropping both is
      // unsafe if a prior batch already informed the backend of the create.
      if (types.has('CREATED') && types.has('DELETED')) {
        if (last.type === 'DELETED' || last.type === 'CREATED') { result.push(last); continue; }
      }
      if (types.has('DIR_CREATED') && types.has('DIR_DELETED')) {
        if (last.type === 'DIR_DELETED' || last.type === 'DIR_CREATED') { result.push(last); continue; }
      }
      if (types.has('CREATED') && types.has('MODIFIED')) {
        const created = pathEvents.find((e) => e.type === 'CREATED');
        if (created) result.push({ ...created, size: last.size, timestamp: last.timestamp });
        continue;
      }
      if (types.has('MODIFIED') && types.has('DELETED')) {
        const deleted = pathEvents.find((e) => e.type === 'DELETED');
        if (deleted) result.push(deleted);
        continue;
      }
      const renameOrMove = pathEvents.find((e) =>
        e.type === 'RENAMED' || e.type === 'MOVED' || e.type === 'DIR_RENAMED' || e.type === 'DIR_MOVED'
      );
      if (renameOrMove && (types.has('MODIFIED') || types.has('CREATED'))) {
        result.push(renameOrMove);
        continue;
      }
      if (pathEvents.every((e) => e.type === 'MODIFIED')) { result.push(last); continue; }
      result.push(last);
    }
    return result;
  }
}
