const fs = require('fs');
const path = require('path');
const { connectorFileSegment } = require('./watcher-state');

const JOURNAL_VERSION = 1;
const MAX_JOURNAL_BYTES = 10 * 1024 * 1024;
const MAX_ROTATIONS = 5;
const DEDUP_WINDOW_MS = 2000;

function eventsFingerprint(events) {
  const sorted = [...(events || [])].sort((a, b) => {
    const pa = `${a.path}\0${a.type}`;
    const pb = `${b.path}\0${b.type}`;
    return pa.localeCompare(pb);
  });
  return JSON.stringify(sorted.map((e) => ({
    t: e.type, p: e.path, o: e.oldPath, s: e.size, d: e.isDirectory,
  })));
}

function ensureDir(dirPath) {
  if (!fs.existsSync(dirPath)) fs.mkdirSync(dirPath, { recursive: true });
}

function readLines(filePath) {
  if (!fs.existsSync(filePath)) return [];
  const content = fs.readFileSync(filePath, 'utf8');
  if (!content.trim()) return [];
  return content.split('\n').map((l) => l.trim()).filter(Boolean);
}

function safeParseJson(line) {
  try { return JSON.parse(line); } catch { return null; }
}

function writeFileAtomic(filePath, content) {
  const tmp = `${filePath}.tmp`;
  fs.writeFileSync(tmp, content, 'utf8');
  fs.renameSync(tmp, filePath);
}

class LocalSyncJournal {
  constructor(baseDir) {
    this.baseDir = baseDir;
    ensureDir(this.baseDir);
  }

  getJournalPath(connectorId) {
    return path.join(this.baseDir, `${connectorFileSegment(connectorId)}.jsonl`);
  }

  getCursorPath(connectorId) {
    return path.join(this.baseDir, `${connectorFileSegment(connectorId)}.cursor.json`);
  }

  getMetaPath(connectorId) {
    // keep backward-compatible filename (non-segmented) since existing installs
    // have already written <connectorId>.meta.json
    return path.join(this.baseDir, `${connectorId}.meta.json`);
  }

  listConnectorIds() {
    ensureDir(this.baseDir);
    return fs.readdirSync(this.baseDir)
      .filter((name) => name.endsWith('.meta.json'))
      .map((name) => name.replace(/\.meta\.json$/, ''));
  }

  getMeta(connectorId) {
    const p = this.getMetaPath(connectorId);
    if (!fs.existsSync(p)) return null;
    try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch { return null; }
  }

  setMeta(connectorId, meta) {
    const p = this.getMetaPath(connectorId);
    const merged = { connectorId, updatedAt: Date.now(), ...meta };
    writeFileAtomic(p, JSON.stringify(merged, null, 2));
    return merged;
  }

  readCursor(connectorId) {
    const p = this.getCursorPath(connectorId);
    if (!fs.existsSync(p)) return { lastAckBatchId: null, lastRecordedBatchId: null };
    try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch {
      return { lastAckBatchId: null, lastRecordedBatchId: null };
    }
  }

  writeCursor(connectorId, cursor) {
    const p = this.getCursorPath(connectorId);
    writeFileAtomic(p, JSON.stringify({ updatedAt: Date.now(), ...cursor }, null, 2));
  }

  listBatches(connectorId) {
    return readLines(this.getJournalPath(connectorId)).map(safeParseJson).filter(Boolean);
  }

  maybeRotate(connectorId) {
    const p = this.getJournalPath(connectorId);
    if (!fs.existsSync(p)) return;
    let size = 0;
    try { size = fs.statSync(p).size; } catch { return; }
    if (size < MAX_JOURNAL_BYTES) return;

    // Drop synced batches to reduce file; only keep pending+failed + recent synced.
    const batches = this.listBatches(connectorId);
    const kept = batches.filter((b) => b.status === 'pending' || b.status === 'failed');
    const text = kept.map((b) => JSON.stringify(b)).join('\n');
    writeFileAtomic(p, text ? `${text}\n` : '');

    // Rotation archive
    for (let i = MAX_ROTATIONS - 1; i >= 1; i -= 1) {
      const from = `${p}.${i}`;
      const to = `${p}.${i + 1}`;
      if (fs.existsSync(from)) {
        try { fs.renameSync(from, to); } catch { /* ignore */ }
      }
    }
  }

  appendBatch(connectorId, batch) {
    this.maybeRotate(connectorId);
    const p = this.getJournalPath(connectorId);
    const fp = eventsFingerprint(batch.events || []);
    const now = Date.now();

    // Drop if identical batch was recorded within the dedup window.
    const existing = this.listBatches(connectorId);
    const last = existing.length > 0 ? existing[existing.length - 1] : null;
    if (last && last.batchId === batch.batchId) return last;
    if (
      last &&
      last.source === (batch.source || 'live') &&
      last.fingerprint === fp &&
      Math.abs(now - (last.createdAt || 0)) <= DEDUP_WINDOW_MS
    ) {
      return last;
    }

    const record = {
      version: JOURNAL_VERSION,
      connectorId,
      status: 'pending',
      attemptCount: 0,
      source: batch.source || 'live',
      fingerprint: fp,
      createdAt: now,
      updatedAt: now,
      ...batch,
    };
    fs.appendFileSync(p, `${JSON.stringify(record)}\n`, 'utf8');
    const cursor = this.readCursor(connectorId);
    this.writeCursor(connectorId, { ...cursor, lastRecordedBatchId: record.batchId });
    return record;
  }

  updateBatchStatus(connectorId, batchId, status, extra = {}) {
    const p = this.getJournalPath(connectorId);
    const batches = this.listBatches(connectorId);
    let incrementAttempt = status === 'failed';
    const next = batches.map((b) => {
      if (b.batchId !== batchId) return b;
      const updated = {
        ...b, status, updatedAt: Date.now(), ...extra,
      };
      if (incrementAttempt) updated.attemptCount = (b.attemptCount || 0) + 1;
      return updated;
    });
    const text = next.map((b) => JSON.stringify(b)).join('\n');
    writeFileAtomic(p, text ? `${text}\n` : '');
    if (status === 'synced') {
      const cursor = this.readCursor(connectorId);
      this.writeCursor(connectorId, { ...cursor, lastAckBatchId: batchId });
    }
  }

  getPendingOrFailedBatches(connectorId) {
    return this.listBatches(connectorId).filter(
      (b) => b.status === 'pending' || b.status === 'failed'
    );
  }

  /**
   * Returns batches to replay. Matches CLI's `readReplayableWatchBatches`:
   * - default: only `pending` + `failed` (incremental resync)
   * - { includeSynced: true }: every batch in the journal (full resync)
   */
  getReplayableBatches(connectorId, opts) {
    const all = this.listBatches(connectorId);
    if (opts && opts.includeSynced === true) return all;
    return all.filter((b) => b.status === 'pending' || b.status === 'failed');
  }

  getSummary(connectorId) {
    const batches = this.listBatches(connectorId);
    let pendingCount = 0, failedCount = 0, syncedCount = 0;
    let lastBatchId = null, lastAckAt = null;
    for (const b of batches) {
      lastBatchId = b.batchId || lastBatchId;
      if (b.status === 'pending') pendingCount += 1;
      if (b.status === 'failed') failedCount += 1;
      if (b.status === 'synced') {
        syncedCount += 1;
        lastAckAt = Math.max(lastAckAt || 0, b.updatedAt || 0);
      }
    }
    return { pendingCount, failedCount, syncedCount, lastBatchId, lastAckAt };
  }
}

module.exports = { LocalSyncJournal };
