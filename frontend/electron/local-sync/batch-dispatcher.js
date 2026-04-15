class BatchDispatcher {
  constructor(dispatchFn, opts = {}) {
    this.dispatch = dispatchFn;
    this.maxBatch = opts.maxBatchSize != null ? opts.maxBatchSize : 50;
    this.flushIntervalMs = opts.flushIntervalMs != null ? opts.flushIntervalMs : 1000;
    this.onError = opts.onError || ((err) => console.error('[BatchDispatcher] error:', err));
    this.buffer = [];
    this.flushTimer = null;
    this.flushing = false;
    this.paused = false;
  }

  push(events, opts) {
    if (!events || events.length === 0) return;
    const source = (opts && opts.source) || 'live';
    this.buffer.push({ events: [...events], source });
    if (this.pending >= this.maxBatch) {
      this.flush();
    } else {
      this.scheduleFlush();
    }
  }

  get pending() {
    return this.buffer.reduce((n, c) => n + c.events.length, 0);
  }

  pause() { this.paused = true; }
  resume() {
    this.paused = false;
    if (this.pending > 0) this.scheduleFlush();
  }

  scheduleFlush() {
    if (this.flushTimer) return;
    this.flushTimer = setTimeout(() => {
      this.flushTimer = null;
      this.flush();
    }, this.flushIntervalMs);
  }

  async flush() {
    if (this.flushTimer) { clearTimeout(this.flushTimer); this.flushTimer = null; }
    if (this.buffer.length === 0 || this.flushing || this.paused) return;
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

  deduplicate(events) {
    const byPath = new Map();
    for (const ev of events) {
      const arr = byPath.get(ev.path) || [];
      arr.push(ev);
      byPath.set(ev.path, arr);
    }
    const result = [];
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
        result.push({ ...created, size: last.size, timestamp: last.timestamp });
        continue;
      }
      if (types.has('MODIFIED') && types.has('DELETED')) {
        result.push(pathEvents.find((e) => e.type === 'DELETED'));
        continue;
      }
      const renameOrMove = pathEvents.find((e) =>
        e.type === 'RENAMED' || e.type === 'MOVED' || e.type === 'DIR_RENAMED' || e.type === 'DIR_MOVED'
      );
      if (renameOrMove && types.has('MODIFIED')) { result.push(renameOrMove); continue; }
      if (pathEvents.every((e) => e.type === 'MODIFIED')) { result.push(last); continue; }
      result.push(last);
    }
    return result;
  }
}

module.exports = { BatchDispatcher };
