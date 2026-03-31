import * as crypto from "crypto";
import * as fs from "fs";
import * as fsp from "fs/promises";
import * as path from "path";
import { defaultAuthDir } from "../auth/token_store";

/** Legacy single-file name (migrated to per-connector on load). */
export const WATCHER_STATE_FILENAME = "watcher_state.json";
export const WATCHER_STATE_VERSION = 1;

/** Safe fragment for filenames (one watcher state / journal per connector). */
export function connectorFileSegment(connectorInstanceId: string): string {
  const t = connectorInstanceId.trim();
  if (!t) return "unknown";
  return t.replace(/[^a-zA-Z0-9._-]+/g, "_").slice(0, 200);
}

export function watcherStateFilePath(
  connectorInstanceId: string,
  authDir?: string
): string {
  const dir = authDir ? path.resolve(authDir) : defaultAuthDir();
  return path.join(
    dir,
    `watcher_state.${connectorFileSegment(connectorInstanceId)}.json`
  );
}
const QUICK_HASH_MAX_BYTES = 4096;
const SAVE_DEBOUNCE_MS = 5000;

export type FileEventType =
  | "CREATED"
  | "MODIFIED"
  | "DELETED"
  | "RENAMED"
  | "MOVED"
  | "DIR_CREATED"
  | "DIR_DELETED"
  | "DIR_RENAMED"
  | "DIR_MOVED";

export type FileEvent = {
  type: FileEventType;
  path: string;
  oldPath?: string;
  timestamp: number;
  size?: number;
  isDirectory: boolean;
};

export type FileEntry = {
  inode: number;
  size: number;
  mtimeMs: number;
  quickHash?: string;
  isDirectory: boolean;
};

export type WatcherState = {
  version: number;
  syncRoot: string;
  connectorInstanceId: string;
  lastScanTimestamp: number;
  files: Record<string, FileEntry>;
};

function toPosixRelKey(rel: string): string {
  return rel.split(path.sep).join("/");
}

/** Stable relative path key (forward slashes) under sync root. */
export function normalizeRelKey(absPath: string, syncRoot: string): string {
  const rel = path.relative(syncRoot, absPath);
  if (rel === "" || rel === ".") {
    return "";
  }
  return toPosixRelKey(rel);
}

function dirnamePosix(p: string): string {
  const i = p.lastIndexOf("/");
  return i <= 0 ? "" : p.slice(0, i);
}

function isValidInode(ino: number | bigint | undefined): boolean {
  if (ino === undefined) return false;
  const n = typeof ino === "bigint" ? Number(ino) : ino;
  return Number.isFinite(n) && n > 0;
}

async function computeQuickHash(
  absFilePath: string,
  size: number,
  mtimeMs: number
): Promise<string | undefined> {
  try {
    const fh = await fsp.open(absFilePath, "r");
    try {
      const buf = Buffer.allocUnsafe(
        Math.min(QUICK_HASH_MAX_BYTES, Math.max(0, size))
      );
      let read = 0;
      if (buf.length > 0) {
        const { bytesRead } = await fh.read(buf, 0, buf.length, 0);
        read = bytesRead;
      }
      const h = crypto.createHash("sha256");
      h.update(buf.subarray(0, read));
      h.update(`|${size}|${mtimeMs}`);
      return h.digest("hex");
    } finally {
      await fh.close();
    }
  } catch {
    return undefined;
  }
}

function parseFileEntry(raw: unknown): FileEntry | null {
  if (typeof raw !== "object" || raw === null) return null;
  const o = raw as Record<string, unknown>;
  const inode = Number(o.inode);
  const size = Number(o.size);
  const mtimeMs = Number(o.mtimeMs);
  if (!Number.isFinite(inode) || !Number.isFinite(size) || !Number.isFinite(mtimeMs)) {
    return null;
  }
  const isDirectory = Boolean(o.isDirectory);
  const quickHash =
    typeof o.quickHash === "string" && o.quickHash.length > 0
      ? o.quickHash
      : undefined;
  return { inode, size, mtimeMs, isDirectory, quickHash };
}

function emptyState(
  syncRoot: string,
  connectorInstanceId: string
): WatcherState {
  return {
    version: WATCHER_STATE_VERSION,
    syncRoot,
    connectorInstanceId,
    lastScanTimestamp: 0,
    files: {},
  };
}

export type ScanSyncRootOptions = {
  includeSubfolders?: boolean;
};

/**
 * Walk sync root (lstat only; does not follow symlinks into targets).
 * Keys are POSIX-style paths relative to syncRoot (empty string = root itself omitted as file entry).
 */
export async function scanSyncRoot(
  syncRootAbs: string,
  options?: ScanSyncRootOptions
): Promise<Map<string, FileEntry>> {
  const includeSubfolders = options?.includeSubfolders !== false;
  const root = path.resolve(syncRootAbs);
  const out = new Map<string, FileEntry>();

  async function visit(dirAbs: string, depth: number): Promise<void> {
    let entries: fs.Dirent[];
    try {
      entries = await fsp.readdir(dirAbs, { withFileTypes: true });
    } catch {
      return;
    }

    for (const ent of entries) {
      if (ent.name === "." || ent.name === "..") continue;
      const abs = path.join(dirAbs, ent.name);
      let st: fs.Stats;
      try {
        st = await fsp.lstat(abs);
      } catch {
        continue;
      }

      const relKey = normalizeRelKey(abs, root);
      const isDirectory = st.isDirectory();
      const inode = st.ino;
      const size = st.isFile() ? st.size : 0;
      const mtimeMs = st.mtimeMs;
      let quickHash: string | undefined;
      if (st.isFile()) {
        quickHash = await computeQuickHash(abs, size, mtimeMs);
      }

      out.set(relKey, {
        inode: typeof inode === "bigint" ? Number(inode) : inode,
        size,
        mtimeMs,
        isDirectory,
        quickHash,
      });

      if (isDirectory && includeSubfolders) {
        await visit(abs, depth + 1);
      }
    }
  }

  await visit(root, 0);
  return out;
}

function eventTypeForCreated(isDir: boolean): FileEventType {
  return isDir ? "DIR_CREATED" : "CREATED";
}

function eventTypeForDeleted(isDir: boolean): FileEventType {
  return isDir ? "DIR_DELETED" : "DELETED";
}

function eventTypeForRenamed(isDir: boolean): FileEventType {
  return isDir ? "DIR_RENAMED" : "RENAMED";
}

function eventTypeForMoved(isDir: boolean): FileEventType {
  return isDir ? "DIR_MOVED" : "MOVED";
}

export type WatcherStateStoreOptions = {
  authDir?: string;
  syncRoot: string;
  connectorInstanceId: string;
  saveDebounceMs?: number;
};

export class WatcherStateStore {
  private readonly authDir: string;
  private debounceMs: number;
  private syncRoot: string;
  private connectorInstanceId: string;
  private state: WatcherState;
  private saveTimer: ReturnType<typeof setTimeout> | null = null;
  private dirty = false;

  constructor(opts: WatcherStateStoreOptions) {
    this.authDir = opts.authDir
      ? path.resolve(opts.authDir)
      : defaultAuthDir();
    this.debounceMs = opts.saveDebounceMs ?? SAVE_DEBOUNCE_MS;
    this.syncRoot = path.resolve(opts.syncRoot);
    this.connectorInstanceId = opts.connectorInstanceId.trim();
    this.state = emptyState(this.syncRoot, this.connectorInstanceId);
  }

  authDirPath(): string {
    return this.authDir;
  }

  statePath(): string {
    return watcherStateFilePath(this.connectorInstanceId, this.authDir);
  }

  setContext(syncRoot: string, connectorInstanceId: string): void {
    const nextRoot = path.resolve(syncRoot);
    const nextId = connectorInstanceId.trim();
    if (
      path.resolve(this.state.syncRoot) !== nextRoot ||
      this.state.connectorInstanceId !== nextId
    ) {
      this.state = emptyState(nextRoot, nextId);
    }
    this.syncRoot = nextRoot;
    this.connectorInstanceId = nextId;
  }

  getSnapshot(): Readonly<WatcherState> {
    return this.state;
  }

  load(): void {
    const p = this.statePath();
    if (!fs.existsSync(p)) {
      const legacy = path.join(this.authDir, WATCHER_STATE_FILENAME);
      if (fs.existsSync(legacy)) {
        try {
          const raw = JSON.parse(fs.readFileSync(legacy, "utf8")) as Record<
            string,
            unknown
          >;
          const fileCid =
            typeof raw.connectorInstanceId === "string"
              ? raw.connectorInstanceId.trim()
              : "";
          if (fileCid === this.connectorInstanceId) {
            try {
              fs.renameSync(legacy, p);
            } catch {
              /* copy if cross-device */
              fs.copyFileSync(legacy, p);
              try {
                fs.unlinkSync(legacy);
              } catch {
                /* ignore */
              }
            }
          }
        } catch {
          /* ignore */
        }
      }
    }
    if (!fs.existsSync(p)) {
      this.state = emptyState(this.syncRoot, this.connectorInstanceId);
      return;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(fs.readFileSync(p, "utf8")) as unknown;
    } catch {
      this.state = emptyState(this.syncRoot, this.connectorInstanceId);
      return;
    }
    if (typeof parsed !== "object" || parsed === null) {
      this.state = emptyState(this.syncRoot, this.connectorInstanceId);
      return;
    }
    const o = parsed as Record<string, unknown>;
    const version = Number(o.version);
    if (version !== WATCHER_STATE_VERSION) {
      this.state = emptyState(this.syncRoot, this.connectorInstanceId);
      return;
    }
    const fileSyncRoot =
      typeof o.syncRoot === "string" ? path.resolve(o.syncRoot) : "";
    const fileConnectorId =
      typeof o.connectorInstanceId === "string"
        ? o.connectorInstanceId.trim()
        : "";
    const lastScanTimestamp = Number(o.lastScanTimestamp);
    const filesRaw = o.files;
    if (
      fileSyncRoot !== this.syncRoot ||
      fileConnectorId !== this.connectorInstanceId
    ) {
      this.state = emptyState(this.syncRoot, this.connectorInstanceId);
      return;
    }
    const files: Record<string, FileEntry> = {};
    if (filesRaw && typeof filesRaw === "object" && filesRaw !== null) {
      for (const [k, v] of Object.entries(filesRaw as Record<string, unknown>)) {
        const normKey = k.split("\\").join("/");
        const entry = parseFileEntry(v);
        if (entry) {
          files[normKey] = entry;
        }
      }
    }
    this.state = {
      version: WATCHER_STATE_VERSION,
      syncRoot: this.syncRoot,
      connectorInstanceId: this.connectorInstanceId,
      lastScanTimestamp: Number.isFinite(lastScanTimestamp)
        ? lastScanTimestamp
        : 0,
      files,
    };
  }

  applyScan(
    entries: Map<string, FileEntry> | Record<string, FileEntry>
  ): void {
    const next: Record<string, FileEntry> = {};
    if (entries instanceof Map) {
      for (const [k, v] of entries) {
        next[k.split("\\").join("/")] = { ...v };
      }
    } else {
      for (const [k, v] of Object.entries(entries)) {
        next[k.split("\\").join("/")] = { ...v };
      }
    }
    this.state.files = next;
    this.state.syncRoot = this.syncRoot;
    this.state.connectorInstanceId = this.connectorInstanceId;
    this.state.lastScanTimestamp = Date.now();
    this.scheduleSave();
  }

  /**
   * Diff previous persisted view vs current scan. Does not mutate `files` until you call `applyScan`.
   */
  reconcile(currentScan: Map<string, FileEntry>): FileEvent[] {
    const now = Date.now();
    const oldFiles = this.state.files;
    const oldPaths = new Set(Object.keys(oldFiles));
    const newPaths = new Set(currentScan.keys());

    const events: FileEvent[] = [];

    type InodeGroup = { paths: string[] };
    const oldByInode = new Map<number, InodeGroup>();
    const newByInode = new Map<number, InodeGroup>();

    for (const p of oldPaths) {
      const e = oldFiles[p];
      if (!e) continue;
      if (isValidInode(e.inode)) {
        let g = oldByInode.get(e.inode);
        if (!g) {
          g = { paths: [] };
          oldByInode.set(e.inode, g);
        }
        g.paths.push(p);
      }
    }
    for (const p of newPaths) {
      const e = currentScan.get(p);
      if (!e) continue;
      if (isValidInode(e.inode)) {
        let g = newByInode.get(e.inode);
        if (!g) {
          g = { paths: [] };
          newByInode.set(e.inode, g);
        }
        g.paths.push(p);
      }
    }

    const handledOld = new Set<string>();
    const handledNew = new Set<string>();

    for (const [ino, oldG] of oldByInode) {
      const newG = newByInode.get(ino);
      if (!newG) continue;
      if (oldG.paths.length !== 1 || newG.paths.length !== 1) continue;
      const oldPath = oldG.paths[0]!;
      const newPath = newG.paths[0]!;
      if (oldPath === newPath) continue;

      const oldEnt = oldFiles[oldPath]!;
      const newEnt = currentScan.get(newPath)!;
      if (oldEnt.isDirectory !== newEnt.isDirectory) {
        continue;
      }

      const sameDir = dirnamePosix(oldPath) === dirnamePosix(newPath);
      const type = sameDir
        ? eventTypeForRenamed(newEnt.isDirectory)
        : eventTypeForMoved(newEnt.isDirectory);

      events.push({
        type,
        path: newPath,
        oldPath,
        timestamp: now,
        size: newEnt.isDirectory ? undefined : newEnt.size,
        isDirectory: newEnt.isDirectory,
      });
      handledOld.add(oldPath);
      handledNew.add(newPath);
    }

    for (const p of oldPaths) {
      if (handledOld.has(p)) continue;
      if (!newPaths.has(p)) continue;
      const oldEnt = oldFiles[p]!;
      const newEnt = currentScan.get(p)!;

      if (oldEnt.isDirectory !== newEnt.isDirectory) {
        events.push({
          type: eventTypeForDeleted(oldEnt.isDirectory),
          path: p,
          timestamp: now,
          isDirectory: oldEnt.isDirectory,
        });
        events.push({
          type: eventTypeForCreated(newEnt.isDirectory),
          path: p,
          timestamp: now,
          size: newEnt.isDirectory ? undefined : newEnt.size,
          isDirectory: newEnt.isDirectory,
        });
        handledOld.add(p);
        handledNew.add(p);
        continue;
      }

      const inodeSame =
        oldEnt.inode === newEnt.inode ||
        (!isValidInode(oldEnt.inode) && !isValidInode(newEnt.inode));
      const metaSame =
        oldEnt.size === newEnt.size &&
        oldEnt.mtimeMs === newEnt.mtimeMs &&
        oldEnt.quickHash === newEnt.quickHash;

      if (!inodeSame || !metaSame) {
        events.push({
          type: "MODIFIED",
          path: p,
          timestamp: now,
          size: newEnt.isDirectory ? undefined : newEnt.size,
          isDirectory: newEnt.isDirectory,
        });
      }
      handledOld.add(p);
      handledNew.add(p);
    }

    for (const p of oldPaths) {
      if (handledOld.has(p)) continue;
      const e = oldFiles[p]!;
      events.push({
        type: eventTypeForDeleted(e.isDirectory),
        path: p,
        timestamp: now,
        isDirectory: e.isDirectory,
      });
    }

    for (const p of newPaths) {
      if (handledNew.has(p)) continue;
      const e = currentScan.get(p)!;
      events.push({
        type: eventTypeForCreated(e.isDirectory),
        path: p,
        timestamp: now,
        size: e.isDirectory ? undefined : e.size,
        isDirectory: e.isDirectory,
      });
    }

    return events;
  }

  /** Run reconcile and persist the scan result. */
  commitReconcile(currentScan: Map<string, FileEntry>): FileEvent[] {
    const ev = this.reconcile(currentScan);
    this.applyScan(currentScan);
    this.flushSave();
    return ev;
  }

  scheduleSave(): void {
    this.dirty = true;
    if (this.saveTimer) return;
    this.saveTimer = setTimeout(() => {
      this.saveTimer = null;
      void this.flushSave();
    }, this.debounceMs);
  }

  flushSave(): void {
    if (this.saveTimer) {
      clearTimeout(this.saveTimer);
      this.saveTimer = null;
    }
    if (!this.dirty) return;
    this.dirty = false;
    const p = this.statePath();
    const tmp = `${p}.tmp`;
    try {
      fs.mkdirSync(this.authDir, { recursive: true, mode: 0o700 });
      fs.writeFileSync(
        tmp,
        JSON.stringify(this.state, null, 2),
        "utf8"
      );
      fs.renameSync(tmp, p);
      try {
        fs.chmodSync(p, 0o600);
      } catch {
        /* ignore */
      }
    } catch {
      try {
        if (fs.existsSync(tmp)) fs.unlinkSync(tmp);
      } catch {
        /* ignore */
      }
    }
  }
}
