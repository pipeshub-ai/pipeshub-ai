const fs = require('fs');
const fsp = require('fs/promises');
const path = require('path');
const crypto = require('crypto');

const WATCHER_STATE_VERSION = 1;
const QUICK_HASH_MAX_BYTES = 4096;
const SAVE_DEBOUNCE_MS = 5000;

function connectorFileSegment(connectorInstanceId) {
  const t = String(connectorInstanceId || '').trim();
  if (!t) return 'unknown';
  return t.replace(/[^a-zA-Z0-9._-]+/g, '_').slice(0, 200);
}

function watcherStateFilePath(baseDir, connectorInstanceId) {
  return path.join(baseDir, `watcher_state.${connectorFileSegment(connectorInstanceId)}.json`);
}

function toPosixRelKey(rel) {
  return rel.split(path.sep).join('/');
}

function normalizeRelKey(absPath, syncRoot) {
  const rel = path.relative(syncRoot, absPath);
  if (rel === '' || rel === '.') return '';
  // NFC so macOS HFS+/APFS NFD filenames hash identically to user-space
  // NFC paths on the Python side. Without this, a CREATED in NFC and a
  // RENAMED whose oldPath chokidar reports in NFD compute different
  // external_record_ids and the server treats them as unrelated files.
  return toPosixRelKey(rel).normalize('NFC');
}

function dirnamePosix(p) {
  const i = p.lastIndexOf('/');
  return i <= 0 ? '' : p.slice(0, i);
}

function isValidInode(ino) {
  if (ino === undefined || ino === null) return false;
  const n = typeof ino === 'bigint' ? Number(ino) : ino;
  return Number.isFinite(n) && n > 0;
}

async function computeQuickHash(absFilePath, size) {
  try {
    const fh = await fsp.open(absFilePath, 'r');
    try {
      const buf = Buffer.allocUnsafe(Math.min(QUICK_HASH_MAX_BYTES, Math.max(0, size)));
      let read = 0;
      if (buf.length > 0) {
        const { bytesRead } = await fh.read(buf, 0, buf.length, 0);
        read = bytesRead;
      }
      const h = crypto.createHash('sha256');
      h.update(buf.subarray(0, read));
      h.update(`|${size}|`);
      return h.digest('hex');
    } finally {
      await fh.close();
    }
  } catch {
    return undefined;
  }
}

async function contentQuickHash(absPath) {
  try {
    const st = await fsp.lstat(absPath);
    if (!st.isFile()) return undefined;
    return computeQuickHash(absPath, st.size);
  } catch {
    return undefined;
  }
}

function matchesAnyPattern(patterns, relPath, absPath) {
  for (const p of patterns) {
    if (p instanceof RegExp) {
      if (p.test(absPath) || p.test(relPath)) return true;
    } else if (typeof p === 'string') {
      if (relPath === p || absPath === p) return true;
    }
  }
  return false;
}

async function scanSyncRoot(syncRootAbs, options = {}) {
  const includeSubfolders = options.includeSubfolders !== false;
  const previousByRelPath = options.previousByRelPath;
  const ignoredPatterns = options.ignoredPatterns || [];
  const root = path.resolve(syncRootAbs);
  const out = new Map();

  async function visit(dirAbs) {
    let entries;
    try {
      entries = await fsp.readdir(dirAbs, { withFileTypes: true });
    } catch {
      return;
    }
    for (const ent of entries) {
      if (ent.name === '.' || ent.name === '..') continue;
      const abs = path.join(dirAbs, ent.name);
      const relKey = normalizeRelKey(abs, root);
      if (matchesAnyPattern(ignoredPatterns, relKey, abs)) continue;
      let st;
      try {
        st = await fsp.lstat(abs);
      } catch {
        continue;
      }
      const isDirectory = st.isDirectory();
      const inode = typeof st.ino === 'bigint' ? Number(st.ino) : st.ino;
      const size = st.isFile() ? st.size : 0;
      const mtimeMs = st.mtimeMs;
      let quickHash;
      if (st.isFile()) {
        const old = previousByRelPath && previousByRelPath.get(relKey);
        if (old && !old.isDirectory && old.size === size && old.mtimeMs === mtimeMs && old.quickHash) {
          quickHash = old.quickHash;
        } else {
          quickHash = await computeQuickHash(abs, size);
        }
      }
      out.set(relKey, { inode, size, mtimeMs, isDirectory, quickHash });
      if (isDirectory && includeSubfolders) {
        await visit(abs);
      }
    }
  }

  await visit(root);
  return out;
}

function emptyState(syncRoot, connectorInstanceId) {
  return {
    version: WATCHER_STATE_VERSION,
    syncRoot,
    connectorInstanceId,
    lastScanTimestamp: 0,
    files: {},
  };
}

function parseFileEntry(raw) {
  if (typeof raw !== 'object' || raw === null) return null;
  const inode = Number(raw.inode);
  const size = Number(raw.size);
  const mtimeMs = Number(raw.mtimeMs);
  if (!Number.isFinite(inode) || !Number.isFinite(size) || !Number.isFinite(mtimeMs)) return null;
  const isDirectory = Boolean(raw.isDirectory);
  const quickHash = typeof raw.quickHash === 'string' && raw.quickHash.length > 0 ? raw.quickHash : undefined;
  return { inode, size, mtimeMs, isDirectory, quickHash };
}

class WatcherStateStore {
  constructor({ baseDir, syncRoot, connectorInstanceId, saveDebounceMs }) {
    this.baseDir = path.resolve(baseDir);
    this.debounceMs = saveDebounceMs != null ? saveDebounceMs : SAVE_DEBOUNCE_MS;
    this.syncRoot = path.resolve(syncRoot);
    this.connectorInstanceId = String(connectorInstanceId).trim();
    this.state = emptyState(this.syncRoot, this.connectorInstanceId);
    this.saveTimer = null;
    this.dirty = false;
  }

  statePath() {
    return watcherStateFilePath(this.baseDir, this.connectorInstanceId);
  }

  getSnapshot() {
    return this.state;
  }

  load() {
    const p = this.statePath();
    if (!fs.existsSync(p)) {
      this.state = emptyState(this.syncRoot, this.connectorInstanceId);
      return;
    }
    let parsed;
    try {
      parsed = JSON.parse(fs.readFileSync(p, 'utf8'));
    } catch {
      this.state = emptyState(this.syncRoot, this.connectorInstanceId);
      return;
    }
    if (typeof parsed !== 'object' || parsed === null) {
      this.state = emptyState(this.syncRoot, this.connectorInstanceId);
      return;
    }
    const version = Number(parsed.version);
    if (version !== 1 && version !== 2) {
      this.state = emptyState(this.syncRoot, this.connectorInstanceId);
      return;
    }
    const fileSyncRoot = typeof parsed.syncRoot === 'string' ? path.resolve(parsed.syncRoot) : '';
    const fileConnectorId = typeof parsed.connectorInstanceId === 'string' ? parsed.connectorInstanceId.trim() : '';
    if (fileSyncRoot !== this.syncRoot || fileConnectorId !== this.connectorInstanceId) {
      this.state = emptyState(this.syncRoot, this.connectorInstanceId);
      return;
    }
    const files = {};
    if (parsed.files && typeof parsed.files === 'object') {
      for (const [k, v] of Object.entries(parsed.files)) {
        const norm = k.split('\\').join('/');
        const entry = parseFileEntry(v);
        if (entry) files[norm] = entry;
      }
    }
    this.state = {
      version: WATCHER_STATE_VERSION,
      syncRoot: this.syncRoot,
      connectorInstanceId: this.connectorInstanceId,
      lastScanTimestamp: Number.isFinite(Number(parsed.lastScanTimestamp)) ? Number(parsed.lastScanTimestamp) : 0,
      files,
    };
  }

  applyScan(entries) {
    const next = {};
    if (entries instanceof Map) {
      for (const [k, v] of entries) next[k.split('\\').join('/')] = { ...v };
    } else {
      for (const [k, v] of Object.entries(entries)) next[k.split('\\').join('/')] = { ...v };
    }
    this.state.files = next;
    this.state.syncRoot = this.syncRoot;
    this.state.connectorInstanceId = this.connectorInstanceId;
    this.state.lastScanTimestamp = Date.now();
    this.scheduleSave();
  }

  reconcile(currentScan) {
    const now = Date.now();
    const oldFiles = this.state.files;
    const oldPaths = new Set(Object.keys(oldFiles));
    const newPaths = new Set(currentScan.keys());
    const events = [];
    const oldByInode = new Map();
    const newByInode = new Map();

    for (const p of oldPaths) {
      const e = oldFiles[p];
      if (!e || !isValidInode(e.inode)) continue;
      let g = oldByInode.get(e.inode);
      if (!g) { g = { paths: [] }; oldByInode.set(e.inode, g); }
      g.paths.push(p);
    }
    for (const p of newPaths) {
      const e = currentScan.get(p);
      if (!e || !isValidInode(e.inode)) continue;
      let g = newByInode.get(e.inode);
      if (!g) { g = { paths: [] }; newByInode.set(e.inode, g); }
      g.paths.push(p);
    }

    const handledOld = new Set();
    const handledNew = new Set();

    for (const [ino, oldG] of oldByInode) {
      const newG = newByInode.get(ino);
      if (!newG) continue;
      if (oldG.paths.length !== 1 || newG.paths.length !== 1) continue;
      const oldPath = oldG.paths[0];
      const newPath = newG.paths[0];
      if (oldPath === newPath) continue;
      const oldEnt = oldFiles[oldPath];
      const newEnt = currentScan.get(newPath);
      if (oldEnt.isDirectory !== newEnt.isDirectory) continue;
      const sameDir = dirnamePosix(oldPath) === dirnamePosix(newPath);
      const type = newEnt.isDirectory
        ? (sameDir ? 'DIR_RENAMED' : 'DIR_MOVED')
        : (sameDir ? 'RENAMED' : 'MOVED');
      events.push({
        type, path: newPath, oldPath, timestamp: now,
        size: newEnt.isDirectory ? undefined : newEnt.size,
        isDirectory: newEnt.isDirectory,
      });
      handledOld.add(oldPath);
      handledNew.add(newPath);
    }

    for (const p of oldPaths) {
      if (handledOld.has(p)) continue;
      if (!newPaths.has(p)) continue;
      const oldEnt = oldFiles[p];
      const newEnt = currentScan.get(p);
      if (oldEnt.isDirectory !== newEnt.isDirectory) {
        events.push({ type: oldEnt.isDirectory ? 'DIR_DELETED' : 'DELETED', path: p, timestamp: now, isDirectory: oldEnt.isDirectory });
        events.push({ type: newEnt.isDirectory ? 'DIR_CREATED' : 'CREATED', path: p, timestamp: now, size: newEnt.isDirectory ? undefined : newEnt.size, isDirectory: newEnt.isDirectory });
        handledOld.add(p); handledNew.add(p);
        continue;
      }
      const inodeSame = oldEnt.inode === newEnt.inode || (!isValidInode(oldEnt.inode) && !isValidInode(newEnt.inode));
      let metaSame;
      if (newEnt.isDirectory) {
        metaSame = oldEnt.mtimeMs === newEnt.mtimeMs;
      } else if (oldEnt.quickHash && newEnt.quickHash) {
        metaSame = oldEnt.size === newEnt.size && oldEnt.quickHash === newEnt.quickHash;
      } else if (!oldEnt.quickHash && newEnt.quickHash) {
        metaSame = oldEnt.size === newEnt.size && oldEnt.mtimeMs === newEnt.mtimeMs;
      } else {
        metaSame = oldEnt.size === newEnt.size && oldEnt.mtimeMs === newEnt.mtimeMs && oldEnt.quickHash === newEnt.quickHash;
      }
      if (!inodeSame || !metaSame) {
        events.push({ type: 'MODIFIED', path: p, timestamp: now, size: newEnt.isDirectory ? undefined : newEnt.size, isDirectory: newEnt.isDirectory });
      }
      handledOld.add(p); handledNew.add(p);
    }

    for (const p of oldPaths) {
      if (handledOld.has(p)) continue;
      const e = oldFiles[p];
      events.push({ type: e.isDirectory ? 'DIR_DELETED' : 'DELETED', path: p, timestamp: now, isDirectory: e.isDirectory });
    }
    for (const p of newPaths) {
      if (handledNew.has(p)) continue;
      const e = currentScan.get(p);
      events.push({ type: e.isDirectory ? 'DIR_CREATED' : 'CREATED', path: p, timestamp: now, size: e.isDirectory ? undefined : e.size, isDirectory: e.isDirectory });
    }
    return events;
  }

  commitReconcile(currentScan) {
    const ev = this.reconcile(currentScan);
    this.applyScan(currentScan);
    this.flushSave();
    return ev;
  }

  scheduleSave() {
    this.dirty = true;
    if (this.saveTimer) return;
    this.saveTimer = setTimeout(() => {
      this.saveTimer = null;
      this.flushSave();
    }, this.debounceMs);
  }

  flushSave() {
    if (this.saveTimer) { clearTimeout(this.saveTimer); this.saveTimer = null; }
    if (!this.dirty) return;
    this.dirty = false;
    const p = this.statePath();
    const tmp = `${p}.tmp`;
    try {
      fs.mkdirSync(this.baseDir, { recursive: true });
      fs.writeFileSync(tmp, JSON.stringify(this.state, null, 2), 'utf8');
      fs.renameSync(tmp, p);
    } catch {
      try { if (fs.existsSync(tmp)) fs.unlinkSync(tmp); } catch { /* ignore */ }
    }
  }
}

module.exports = {
  WATCHER_STATE_VERSION,
  WatcherStateStore,
  scanSyncRoot,
  contentQuickHash,
  normalizeRelKey,
  connectorFileSegment,
};
