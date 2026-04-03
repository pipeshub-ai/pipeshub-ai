import * as fs from "fs";
import * as path from "path";
import type { FileEvent } from "./watcher_state";
import { connectorFileSegment } from "./watcher_state";

export type WatchJournalSource = "live" | "reconcile";

export type WatchJournalBackendStatus =
  | "pending"
  | "synced"
  | "failed";

type LegacyWatchJournalBackendStatus =
  | WatchJournalBackendStatus
  | "acked"
  | "local_only";

export type WatchJournalLineV1 = {
  v: 1;
  batchId: string;
  recordedAt: number;
  source: WatchJournalSource;
  connectorInstanceId: string;
  syncRoot: string;
  events: FileEvent[];
  backendStatus: LegacyWatchJournalBackendStatus;
  replayEvents?: FileEvent[];
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

function normalizeBackendStatus(
  status: LegacyWatchJournalBackendStatus
): WatchJournalBackendStatus {
  if (status === "acked") {
    return "synced";
  }
  if (status === "local_only") {
    return "pending";
  }
  return status;
}

function normalizeJournalLine(raw: WatchJournalLineV1): WatchJournalLineV1 {
  return {
    ...raw,
    backendStatus: normalizeBackendStatus(raw.backendStatus),
  };
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

const JOURNAL_MAX_BYTES = 10 * 1024 * 1024;
const JOURNAL_ROTATIONS_TO_KEEP = 5;

/** Rotate oversized JSONL journal to `.jsonl.1`, … before append. */
function rotateJournalIfOversized(jpath: string): void {
  if (!fs.existsSync(jpath)) return;
  const st = fs.statSync(jpath);
  if (st.size <= JOURNAL_MAX_BYTES) return;
  const last = `${jpath}.${JOURNAL_ROTATIONS_TO_KEEP}`;
  try {
    if (fs.existsSync(last)) {
      fs.unlinkSync(last);
    }
  } catch {
    /* ignore */
  }
  for (let i = JOURNAL_ROTATIONS_TO_KEEP - 1; i >= 1; i--) {
    const from = `${jpath}.${i}`;
    const to = `${jpath}.${i + 1}`;
    if (!fs.existsSync(from)) continue;
    try {
      fs.renameSync(from, to);
    } catch {
      /* ignore */
    }
  }
  try {
    fs.renameSync(jpath, `${jpath}.1`);
  } catch {
    /* ignore */
  }
}

function readLastJournalLine(jpath: string): WatchJournalLineV1 | null {
  if (!fs.existsSync(jpath)) return null;
  try {
    const stat = fs.statSync(jpath);
    const size = stat.size;
    if (size === 0) return null;
    const tailSize = Math.min(256 * 1024, size);
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
    return normalizeJournalLine(o);
  } catch {
    return null;
  }
}

export function readWatchJournal(
  authDir: string,
  connectorInstanceId: string
): WatchJournalLineV1[] {
  const jpath = watcherEventsJournalPath(authDir, connectorInstanceId);
  if (!fs.existsSync(jpath)) {
    return [];
  }
  try {
    return fs
      .readFileSync(jpath, "utf8")
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => JSON.parse(line) as WatchJournalLineV1)
      .filter((line) => line.v === 1 && Array.isArray(line.events))
      .map((line) => normalizeJournalLine(line));
  } catch {
    return [];
  }
}

export function readReplayableWatchBatches(
  authDir: string,
  connectorInstanceId: string,
  opts?: {
    includeSynced?: boolean;
  }
): WatchJournalLineV1[] {
  const lines = readWatchJournal(authDir, connectorInstanceId);
  if (opts?.includeSynced === true) {
    return lines;
  }
  return lines.filter(
    (line) =>
      line.backendStatus === "pending" || line.backendStatus === "failed"
  );
}

export function updateWatchBatchStatus(
  authDir: string,
  connectorInstanceId: string,
  batchId: string,
  backendStatus: WatchJournalBackendStatus
): void {
  const lines = readWatchJournal(authDir, connectorInstanceId);
  if (lines.length === 0) {
    return;
  }
  let updated = false;
  const nextLines = lines.map((line) => {
    if (line.batchId !== batchId) {
      return line;
    }
    updated = true;
    return {
      ...line,
      backendStatus,
    };
  });
  if (!updated) {
    return;
  }
  const jpath = watcherEventsJournalPath(authDir, connectorInstanceId);
  const tmp = `${jpath}.tmp`;
  fs.writeFileSync(
    tmp,
    `${nextLines.map((line) => JSON.stringify(line)).join("\n")}\n`,
    "utf8"
  );
  fs.renameSync(tmp, jpath);
  try {
    fs.chmodSync(jpath, 0o600);
  } catch {
    /* ignore */
  }

  if (backendStatus !== "synced") {
    return;
  }

  const syncedLine = nextLines.find((line) => line.batchId === batchId);
  if (!syncedLine) {
    return;
  }

  const cur = readCursor(authDir, connectorInstanceId, syncedLine.syncRoot);
  cur.syncRoot = syncedLine.syncRoot;
  cur.lastAckBatchId = batchId;
  cur.lastAckAt = Date.now();
  if (!cur.lastRecordedBatchId) {
    cur.lastRecordedBatchId = batchId;
    cur.lastRecordedAt = syncedLine.recordedAt;
  }
  try {
    atomicWriteJson(watcherCursorPath(authDir, connectorInstanceId), cur);
  } catch {
    /* ignore */
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
    replayEvents?: FileEvent[];
  }
): void {
  if (
    args.events.length === 0 &&
    (!Array.isArray(args.replayEvents) || args.replayEvents.length === 0)
  ) {
    return;
  }
  fs.mkdirSync(authDir, { recursive: true, mode: 0o700 });
  const jpath = watcherEventsJournalPath(authDir, args.connectorInstanceId);
  rotateJournalIfOversized(jpath);

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
    replayEvents: args.replayEvents,
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
  if (args.backendStatus === "synced") {
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
