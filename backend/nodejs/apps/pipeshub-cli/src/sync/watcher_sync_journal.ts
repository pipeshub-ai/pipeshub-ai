import * as fs from "fs";
import * as path from "path";
import type { FileEvent } from "./watcher_state";
import { connectorFileSegment } from "./watcher_state";

export type WatchJournalSource = "live" | "reconcile";

export type WatchJournalBackendStatus =
  | "acked"
  | "failed"
  | "local_only";

export type WatchJournalLineV1 = {
  v: 1;
  batchId: string;
  recordedAt: number;
  source: WatchJournalSource;
  connectorInstanceId: string;
  syncRoot: string;
  events: FileEvent[];
  backendStatus: WatchJournalBackendStatus;
};

export type ConnectorWatchCursorV1 = {
  v: 1;
  connectorInstanceId: string;
  syncRoot: string;
  /** Last batch the connector backend accepted (HTTP 2xx), if any. */
  lastAckBatchId: string | null;
  lastAckAt: number | null;
  /** Last batch written to the journal (may not be acked). */
  lastRecordedBatchId: string | null;
  lastRecordedAt: number | null;
};

export function watcherEventsJournalPath(
  authDir: string,
  connectorInstanceId: string
): string {
  return path.join(
    authDir,
    `watcher_events.${connectorFileSegment(connectorInstanceId)}.jsonl`
  );
}

export function watcherCursorPath(
  authDir: string,
  connectorInstanceId: string
): string {
  return path.join(
    authDir,
    `watcher_cursor.${connectorFileSegment(connectorInstanceId)}.json`
  );
}

function readCursor(
  authDir: string,
  connectorInstanceId: string,
  syncRoot: string
): ConnectorWatchCursorV1 {
  const p = watcherCursorPath(authDir, connectorInstanceId);
  if (!fs.existsSync(p)) {
    return {
      v: 1,
      connectorInstanceId,
      syncRoot,
      lastAckBatchId: null,
      lastAckAt: null,
      lastRecordedBatchId: null,
      lastRecordedAt: null,
    };
  }
  try {
    const o = JSON.parse(fs.readFileSync(p, "utf8")) as Record<string, unknown>;
    if (Number(o.v) !== 1) throw new Error("bad version");
    return {
      v: 1,
      connectorInstanceId,
      syncRoot,
      lastAckBatchId:
        typeof o.lastAckBatchId === "string" ? o.lastAckBatchId : null,
      lastAckAt:
        typeof o.lastAckAt === "number" && Number.isFinite(o.lastAckAt)
          ? o.lastAckAt
          : null,
      lastRecordedBatchId:
        typeof o.lastRecordedBatchId === "string"
          ? o.lastRecordedBatchId
          : null,
      lastRecordedAt:
        typeof o.lastRecordedAt === "number" &&
        Number.isFinite(o.lastRecordedAt)
          ? o.lastRecordedAt
          : null,
    };
  } catch {
    return {
      v: 1,
      connectorInstanceId,
      syncRoot,
      lastAckBatchId: null,
      lastAckAt: null,
      lastRecordedBatchId: null,
      lastRecordedAt: null,
    };
  }
}

function atomicWriteJson(filePath: string, data: unknown): void {
  const tmp = `${filePath}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2), "utf8");
  fs.renameSync(tmp, filePath);
  try {
    fs.chmodSync(filePath, 0o600);
  } catch {
    /* ignore */
  }
}

/** Stable fingerprint for deduplicating duplicate batches in the journal. */
function eventsFingerprint(events: FileEvent[]): string {
  const sorted = [...events].sort((a, b) => {
    const pa = `${a.path}\0${a.type}`;
    const pb = `${b.path}\0${b.type}`;
    return pa.localeCompare(pb);
  });
  return JSON.stringify(
    sorted.map((e) => ({
      t: e.type,
      p: e.path,
      o: e.oldPath,
      s: e.size,
      d: e.isDirectory,
    }))
  );
}

function readLastJournalLine(jpath: string): WatchJournalLineV1 | null {
  if (!fs.existsSync(jpath)) return null;
  try {
    const stat = fs.statSync(jpath);
    const size = stat.size;
    if (size === 0) return null;
    const tailSize = Math.min(64 * 1024, size);
    const buf = Buffer.alloc(tailSize);
    const fd = fs.openSync(jpath, "r");
    try {
      fs.readSync(fd, buf, 0, tailSize, size - tailSize);
    } finally {
      fs.closeSync(fd);
    }
    const text = buf.toString("utf8");
    const lines = text.split("\n").filter((l) => l.trim().length > 0);
    const last = lines[lines.length - 1];
    if (!last) return null;
    const o = JSON.parse(last) as WatchJournalLineV1;
    if (o.v !== 1 || !Array.isArray(o.events)) return null;
    return o;
  } catch {
    return null;
  }
}

const DEDUP_WINDOW_MS = 2000;

/**
 * Append one batch line to the connector's JSONL history and refresh cursor.
 * Idempotent per batchId; skips near-duplicate lines (same events within a short window).
 */
export function recordWatchBatch(
  authDir: string,
  args: {
    connectorInstanceId: string;
    syncRoot: string;
    batchId: string;
    source: WatchJournalSource;
    events: FileEvent[];
    backendStatus: WatchJournalBackendStatus;
  }
): void {
  if (args.events.length === 0) {
    return;
  }
  fs.mkdirSync(authDir, { recursive: true, mode: 0o700 });
  const jpath = watcherEventsJournalPath(authDir, args.connectorInstanceId);

  const cur = readCursor(authDir, args.connectorInstanceId, args.syncRoot);
  if (cur.lastRecordedBatchId === args.batchId) {
    return;
  }

  const now = Date.now();
  const fp = eventsFingerprint(args.events);
  const last = readLastJournalLine(jpath);
  if (last) {
    if (last.batchId === args.batchId) {
      return;
    }
    if (
      last.connectorInstanceId === args.connectorInstanceId &&
      last.source === args.source &&
      last.backendStatus === args.backendStatus &&
      eventsFingerprint(last.events) === fp &&
      Math.abs(now - last.recordedAt) <= DEDUP_WINDOW_MS
    ) {
      return;
    }
  }

  const line: WatchJournalLineV1 = {
    v: 1,
    batchId: args.batchId,
    recordedAt: now,
    source: args.source,
    connectorInstanceId: args.connectorInstanceId,
    syncRoot: args.syncRoot,
    events: args.events,
    backendStatus: args.backendStatus,
  };
  try {
    fs.appendFileSync(jpath, `${JSON.stringify(line)}\n`, "utf8");
  } catch (e) {
    console.error(
      `[pipeshub] watcher journal append failed: ${e instanceof Error ? e.message : String(e)}`
    );
    throw e;
  }
  try {
    fs.chmodSync(jpath, 0o600);
  } catch {
    /* ignore */
  }

  cur.syncRoot = args.syncRoot;
  cur.lastRecordedBatchId = args.batchId;
  cur.lastRecordedAt = line.recordedAt;
  if (
    args.backendStatus === "acked" ||
    args.backendStatus === "local_only"
  ) {
    cur.lastAckBatchId = args.batchId;
    cur.lastAckAt = line.recordedAt;
  }
  try {
    atomicWriteJson(watcherCursorPath(authDir, args.connectorInstanceId), cur);
  } catch (e) {
    console.error(
      `[pipeshub] watcher cursor update failed (journal line was written): ${e instanceof Error ? e.message : String(e)}`
    );
  }
}
