/**
 * Centralized debug logger for tracking rerenders, store writes, and spacer recalculations.
 *
 * Instead of logging on every render, counters are accumulated silently and
 * flushed as a single summary when a significant event occurs (stream start,
 * stream complete, chat switch, new chat, etc.).
 *
 * Usage:
 *   Components: `debugLog.tick('[sidebar] [ChatSidebar]')` in render body
 *   Reasons:    `debugLog.reason('[chat] [ChatContent]', ['activeSlotId'])`
 *   Store:      `debugLog.storeWrite(['slots', 'activeSlotId'])` in subscriber
 *   Spacer:     `debugLog.spacer('containerH=800 lastMsgH=200 → spacer=600')`
 *   Events:     `debugLog.flush('stream-completed', { slotId, convId })`
 */

const renderCounts: Record<string, number> = {};
const renderReasons: Record<string, Record<string, number>> = {};
const storeWriteEntries: string[] = [];
const spacerEntries: string[] = [];
let chunkCount = 0;
let rafFlushCount = 0;

export const debugLog = {
  /**
   * Increment the render counter for a keyed component.
   * Call once per render in the component body (not in useEffect).
   */
  tick(key: string): void {
    renderCounts[key] = (renderCounts[key] || 0) + 1;
  },

  /**
   * Record which selector/prop caused a component re-render.
   * Call after comparing current values to previous ref in the render body.
   *
   * @param key       — component identifier (same as tick)
   * @param reasons   — array of changed selector/prop names
   */
  reason(key: string, reasons: string[]): void {
    if (!renderReasons[key]) renderReasons[key] = {};
    for (const r of reasons) {
      renderReasons[key][r] = (renderReasons[key][r] || 0) + 1;
    }
  },

  /**
   * Record a Zustand store state change (called from store subscriber).
   * @param changedFields — which top-level fields changed
   */
  storeWrite(changedFields: string[]): void {
    storeWriteEntries.push(changedFields.join('+'));
  },

  /**
   * Record an incoming SSE chunk.
   */
  chunk(): void {
    chunkCount += 1;
  },

  /**
   * Record a rAF-batched flush to the Zustand store.
   */
  rafFlush(): void {
    rafFlushCount += 1;
  },

  /**
   * Record a spacer recalculation detail line.
   * Accumulated and printed (last 3) on flush.
   */
  spacer(detail: string): void {
    spacerEntries.push(detail);
  },

  /**
   * Flush all accumulated counters and logs to the console,
   * then reset. Call at significant lifecycle events.
   *
   * @param event - Human-readable event name (e.g. 'stream-completed')
   * @param meta  - Optional key-value context printed alongside the event
   */
  flush(event: string, meta?: Record<string, unknown>): void {
    const metaStr = meta
      ? ' ' + Object.entries(meta).map(([k, v]) => `${k}=${v}`).join(' ')
      : '';
    console.log(`[debugging] ── ${event}${metaStr} ──`);

    // Render counts
    const entries = Object.entries(renderCounts).filter(([, c]) => c > 0);
    if (entries.length > 0) {
      for (const [key, count] of entries) {
        const reasons = renderReasons[key];
        const reasonStr = reasons
          ? ' ← ' + Object.entries(reasons)
              .filter(([, c]) => c > 0)
              .map(([r, c]) => `${r}(${c})`)
              .join(', ')
          : '';
        console.log(`[debugging]   ${key}: ${count} renders${reasonStr}`);
      }
    } else {
      console.log(`[debugging]   (no renders since last flush)`);
    }

    // Store writes
    if (storeWriteEntries.length > 0) {
      // Aggregate by field combination
      const writeCounts: Record<string, number> = {};
      for (const entry of storeWriteEntries) {
        writeCounts[entry] = (writeCounts[entry] || 0) + 1;
      }
      const summary = Object.entries(writeCounts)
        .map(([fields, count]) => `${fields}(${count})`)
        .join(', ');
      console.log(`[debugging]   [store] ${storeWriteEntries.length} writes: ${summary}`);
    }

    // Streaming counters
    if (chunkCount > 0 || rafFlushCount > 0) {
      console.log(`[debugging]   [streaming] ${chunkCount} chunks, ${rafFlushCount} rAF flushes`);
    }

    // Spacer recalculations
    if (spacerEntries.length > 0) {
      console.log(`[debugging]   [spacer] ${spacerEntries.length} recalcs`);
      // Print last 3 for context
      const tail = spacerEntries.slice(-3);
      for (const entry of tail) {
        console.log(`[debugging]   [spacer] ${entry}`);
      }
    }

    // Reset
    Object.keys(renderCounts).forEach((k) => { renderCounts[k] = 0; });
    Object.keys(renderReasons).forEach((k) => {
      Object.keys(renderReasons[k]).forEach((r) => { renderReasons[k][r] = 0; });
    });
    storeWriteEntries.length = 0;
    spacerEntries.length = 0;
    chunkCount = 0;
    rafFlushCount = 0;
  },
};
