const fsp = require('fs/promises');
const path = require('path');
const crypto = require('crypto');
const { normalizeRelKey } = require('./watcher-state');

const QUICK_HASH_BYTES = 4096;
const MAX_PENDING_UNLINK_ENTRIES = 10000;

async function quickHash(absPath) {
  try {
    const fh = await fsp.open(absPath, 'r');
    try {
      const stat = await fh.stat();
      const buf = Buffer.allocUnsafe(Math.min(QUICK_HASH_BYTES, Math.max(0, stat.size)));
      let read = 0;
      if (buf.length > 0) {
        const { bytesRead } = await fh.read(buf, 0, buf.length, 0);
        read = bytesRead;
      }
      const h = crypto.createHash('sha256');
      h.update(buf.subarray(0, read));
      h.update(`|${stat.size}|`);
      return h.digest('hex');
    } finally {
      await fh.close();
    }
  } catch {
    return undefined;
  }
}

function isValidInode(ino) {
  return ino !== undefined && Number.isFinite(ino) && ino > 0;
}

function dirnamePosix(p) {
  const i = p.lastIndexOf('/');
  return i <= 0 ? '' : p.slice(0, i);
}

class EventCorrelator {
  constructor(opts) {
    this.syncRoot = path.resolve(opts.syncRoot);
    this.correlationWindowMs = opts.correlationWindowMs != null ? opts.correlationWindowMs : 250;
    this.changeDebounceMs = opts.changeDebounceMs != null ? opts.changeDebounceMs : 300;
    this.shouldSuppressModifiedChange = opts.shouldSuppressModifiedChange;
    this.getPreviousFileEntry = opts.getPreviousFileEntry || (() => undefined);
    this.pendingUnlinks = new Map();
    this.pendingAdds = new Map();
    this.changeTimers = new Map();
    this.pendingChanges = new Map();
    this.flushTimer = null;
    this.onEvents = null;
    this.unlinkInodes = new Map();
  }

  setListener(fn) {
    this.onEvents = fn;
  }

  async push(type, absPath, stats) {
    const relKey = normalizeRelKey(absPath, this.syncRoot);
    if (!relKey) return;
    const isDirectory = type === 'addDir' || type === 'unlinkDir';
    const raw = {
      type, absPath, relKey,
      timestamp: Date.now(),
      inode: stats ? (typeof stats.ino === 'bigint' ? Number(stats.ino) : stats.ino) : undefined,
      size: stats && typeof stats.isFile === 'function' && stats.isFile() ? stats.size : undefined,
      mtimeMs: stats && stats.mtimeMs,
      isDirectory,
    };
    switch (type) {
      case 'unlink':
      case 'unlinkDir': await this.handleUnlink(raw); break;
      case 'add':
      case 'addDir': await this.handleAdd(raw); break;
      case 'change': this.handleChange(raw); break;
    }
  }

  async handleUnlink(raw) {
    if (this.pendingAdds.has(raw.relKey)) {
      const add = this.pendingAdds.get(raw.relKey);
      this.pendingAdds.delete(raw.relKey);
      this.emit([{ type: 'MODIFIED', path: raw.relKey, timestamp: add.timestamp, size: add.size, isDirectory: raw.isDirectory }]);
      return;
    }
    // Chokidar's unlink event typically lacks stats (file is already gone),
    // so recover inode/size/quickHash from the persisted watcher state. Without
    // this, rename detection in flush() can never match by inode or by hash.
    const enriched = { ...raw };
    if (!isValidInode(enriched.inode) || enriched.quickHash === undefined) {
      const prev = this.getPreviousFileEntry(raw.relKey);
      if (prev) {
        if (!isValidInode(enriched.inode) && isValidInode(prev.inode)) enriched.inode = prev.inode;
        if (enriched.size === undefined && !prev.isDirectory) enriched.size = prev.size;
        if (!raw.isDirectory && prev.quickHash) enriched.quickHash = prev.quickHash;
      }
    }
    this.pendingUnlinks.set(raw.relKey, enriched);
    if (isValidInode(enriched.inode)) this.unlinkInodes.set(enriched.inode, enriched);
    this.scheduleFlush();
    if (this.pendingUnlinks.size > MAX_PENDING_UNLINK_ENTRIES) this.flush();
  }

  async handleAdd(raw) {
    let hash;
    if (!raw.isDirectory) hash = await quickHash(raw.absPath);
    const pending = { ...raw, quickHash: hash };

    if (this.pendingUnlinks.has(raw.relKey)) {
      const unlink = this.pendingUnlinks.get(raw.relKey);
      this.pendingUnlinks.delete(raw.relKey);
      if (isValidInode(unlink.inode)) this.unlinkInodes.delete(unlink.inode);
      this.emit([{ type: 'MODIFIED', path: raw.relKey, timestamp: raw.timestamp, size: raw.size, isDirectory: raw.isDirectory }]);
      return;
    }

    if (isValidInode(raw.inode) && this.unlinkInodes.has(raw.inode)) {
      const unlink = this.unlinkInodes.get(raw.inode);
      if (unlink.isDirectory === raw.isDirectory) {
        this.unlinkInodes.delete(raw.inode);
        this.pendingUnlinks.delete(unlink.relKey);
        const sameDir = dirnamePosix(unlink.relKey) === dirnamePosix(raw.relKey);
        const evtType = raw.isDirectory
          ? (sameDir ? 'DIR_RENAMED' : 'DIR_MOVED')
          : (sameDir ? 'RENAMED' : 'MOVED');
        this.emit([{ type: evtType, path: raw.relKey, oldPath: unlink.relKey, timestamp: raw.timestamp, size: raw.size, isDirectory: raw.isDirectory }]);
        return;
      }
    }

    this.pendingAdds.set(raw.relKey, pending);
    this.scheduleFlush();
  }

  handleChange(raw) {
    const existing = this.changeTimers.get(raw.relKey);
    if (existing) clearTimeout(existing);
    this.pendingChanges.set(raw.relKey, raw);
    const relKey = raw.relKey;
    const timer = setTimeout(() => {
      this.changeTimers.delete(relKey);
      this.flushDebouncedChange(relKey).catch(() => { /* ignore */ });
    }, this.changeDebounceMs);
    this.changeTimers.set(raw.relKey, timer);
  }

  async flushDebouncedChange(relKey) {
    const ev = this.pendingChanges.get(relKey);
    if (!ev) return;
    this.pendingChanges.delete(relKey);
    await this.emitModifiedIfNeeded(ev);
  }

  async emitModifiedIfNeeded(ev) {
    if (!ev.isDirectory && this.shouldSuppressModifiedChange) {
      try {
        if (await this.shouldSuppressModifiedChange(ev)) return;
      } catch { /* emit below */ }
    }
    this.emit([{ type: 'MODIFIED', path: ev.relKey, timestamp: ev.timestamp, size: ev.size, isDirectory: ev.isDirectory }]);
  }

  scheduleFlush() {
    if (this.flushTimer) return;
    this.flushTimer = setTimeout(() => {
      this.flushTimer = null;
      this.flush();
    }, this.correlationWindowMs);
  }

  flush() {
    const events = [];
    if (this.pendingUnlinks.size > 0 && this.pendingAdds.size > 0) {
      const unlinksByHash = new Map();
      for (const [, u] of this.pendingUnlinks) {
        if (u.quickHash) {
          const arr = unlinksByHash.get(u.quickHash) || [];
          arr.push(u);
          unlinksByHash.set(u.quickHash, arr);
        }
      }
      for (const [relKey, add] of this.pendingAdds) {
        if (!add.quickHash) continue;
        const matches = unlinksByHash.get(add.quickHash);
        if (!matches || matches.length === 0) continue;
        const idx = matches.findIndex((u) => u.isDirectory === add.isDirectory && this.pendingUnlinks.has(u.relKey));
        if (idx === -1) continue;
        const unlink = matches[idx];
        matches.splice(idx, 1);
        this.pendingUnlinks.delete(unlink.relKey);
        this.pendingAdds.delete(relKey);
        if (isValidInode(unlink.inode)) this.unlinkInodes.delete(unlink.inode);
        const sameDir = dirnamePosix(unlink.relKey) === dirnamePosix(add.relKey);
        const evtType = add.isDirectory
          ? (sameDir ? 'DIR_RENAMED' : 'DIR_MOVED')
          : (sameDir ? 'RENAMED' : 'MOVED');
        events.push({ type: evtType, path: add.relKey, oldPath: unlink.relKey, timestamp: add.timestamp, size: add.size, isDirectory: add.isDirectory });
      }
    }
    for (const [, u] of this.pendingUnlinks) {
      events.push({ type: u.isDirectory ? 'DIR_DELETED' : 'DELETED', path: u.relKey, timestamp: u.timestamp, isDirectory: u.isDirectory });
    }
    this.pendingUnlinks.clear();
    this.unlinkInodes.clear();
    for (const [, a] of this.pendingAdds) {
      events.push({ type: a.isDirectory ? 'DIR_CREATED' : 'CREATED', path: a.relKey, timestamp: a.timestamp, size: a.size, isDirectory: a.isDirectory });
    }
    this.pendingAdds.clear();
    if (events.length > 0) this.emit(events);
  }

  emit(events) {
    if (this.onEvents && events.length > 0) this.onEvents(events);
  }

  async drain() {
    if (this.flushTimer) { clearTimeout(this.flushTimer); this.flushTimer = null; }
    for (const t of this.changeTimers.values()) clearTimeout(t);
    this.changeTimers.clear();
    const pendingList = [...this.pendingChanges.values()];
    this.pendingChanges.clear();
    for (const ev of pendingList) await this.emitModifiedIfNeeded(ev);
    this.flush();
  }
}

module.exports = { EventCorrelator };
