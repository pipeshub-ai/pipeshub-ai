import type { ErrorCodeOf } from '../contracts/generated/ext/pipeshub/errors';
import { createHash } from 'node:crypto';
import { SealContext } from './data-protection';
import type { TurnRole } from './session-roles';

/**
 * Session workspaces (P3.2, ARCHITECTURE §4.3): versioned file manifests
 * with CAS commits, forks and three-way merge. The rules mirror Python's
 * `harness_core.workspace` exactly (path canonical form, manifest hash,
 * merge); `contracts/harness/vectors/workspace.json` pins both.
 */
export const MAX_WORKSPACE_PATH_BYTES = 1024;
export const MAX_WORKSPACE_PATH_DEPTH = 32;
/** One manifest is one Mongo document; this keeps it well under 16 MB. */
export const MAX_WORKSPACE_FILES = 20_000;
export const REPOSITORY_DIR = '.git';
/**
 * Git-like file modes (Python: `FILE_MODE`, `SYMLINK_MODE`): a regular file's
 * permission bits, or a symbolic link whose content is its target. Only a
 * non-default mode is stored and hashed, so older manifests keep their hash.
 */
export const FILE_MODE = 0o100644;
export const SYMLINK_MODE = 0o120777;
const REGULAR_FILE_TYPE = 0o100000;
const PERMISSION_BITS = 0o777;

export const validFileMode = (mode: number): boolean =>
  Number.isInteger(mode) &&
  (mode === SYMLINK_MODE ||
    (mode >= REGULAR_FILE_TYPE &&
      mode <= (REGULAR_FILE_TYPE | PERMISSION_BITS)));
export const WORKSPACE_ID_PREFIX = 'ws:';

export const COMMIT_REASONS = [
  'autosave',
  'turn_end',
  'before_effect',
  'seed',
  'merge',
  'rollback',
] as const;
export type CommitReason = (typeof COMMIT_REASONS)[number];
/** What a worker may name; merge and rollback commits are made by Node. */
export const WORKER_COMMIT_REASONS = [
  'autosave',
  'turn_end',
  'before_effect',
  'seed',
] as const;

/**
 * `partial` (policy `keepTarget` only): the fork's non-conflicting changes
 * merged, the target kept its side of every conflict (Python: `MergeStatus`).
 */
export const MERGE_STATUSES = [
  'merged',
  'up_to_date',
  'conflict',
  'partial',
] as const;
export type MergeStatus = (typeof MERGE_STATUSES)[number];
/** `refuse`: all or nothing; `keepTarget`: merge the rest, keep the target's side of conflicts. */
export const CONFLICT_POLICIES = ['refuse', 'keepTarget'] as const;
export type ConflictPolicy = (typeof CONFLICT_POLICIES)[number];
/** Bounds a writer lease may ask for (P6.4); a writer renews well inside it. */
export const WRITER_LEASE_MIN_MS = 100;
export const WRITER_LEASE_MAX_MS = 10 * 60_000;

export type WorkspaceErrorCode =
  ErrorCodeOf<'workspace'> | 'WORKSPACE_CONFLICT';

/** A typed workspace refusal; the transport maps the code to its status. */
export class WorkspaceError extends Error {
  constructor(
    public readonly code: WorkspaceErrorCode,
    message: string,
  ) {
    super(message);
    this.name = 'WorkspaceError';
  }
}

const invalid = (message: string): WorkspaceError =>
  new WorkspaceError('WORKSPACE_INVALID_CHANGE', message);

const LONE_SURROGATE =
  /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;

/** The canonical relative path a manifest accepts (Python: `normalize_workspace_file_path`). */
export function normalizeWorkspacePath(path: unknown): string {
  if (typeof path !== 'string' || path.length === 0) {
    throw invalid('Workspace file path must be a non-empty string');
  }
  if (path.includes('\u0000') || path.includes('\\')) {
    throw invalid('Workspace file path contains a forbidden character');
  }
  if (LONE_SURROGATE.test(path)) {
    throw invalid('Workspace file path is not valid Unicode');
  }
  const normalized = path.normalize('NFC');
  if (normalized.startsWith('/')) {
    throw invalid('Workspace file path must be relative');
  }
  if (Buffer.byteLength(normalized, 'utf8') > MAX_WORKSPACE_PATH_BYTES) {
    throw invalid('Workspace file path is too long');
  }
  const parts = normalized.split('/');
  if (parts.some((part) => part === '' || part === '.' || part === '..')) {
    throw invalid('Workspace file path must be canonical and cannot traverse');
  }
  if (parts.length > MAX_WORKSPACE_PATH_DEPTH) {
    throw invalid('Workspace file path is too deep');
  }
  return normalized;
}

/** The `.git` directory a canonical path belongs to, or null. */
export function repositoryOf(path: string): string | null {
  const parts = path.split('/');
  const index = parts.indexOf(REPOSITORY_DIR);
  return index < 0 ? null : parts.slice(0, index + 1).join('/');
}

/** Python's `sorted()` on str: by code point, not UTF-16 unit. */
export function compareCodePoints(left: string, right: string): number {
  let i = 0;
  let j = 0;
  while (i < left.length && j < right.length) {
    const a = left.codePointAt(i) ?? 0;
    const b = right.codePointAt(j) ?? 0;
    if (a !== b) return a < b ? -1 : 1;
    i += a > 0xffff ? 2 : 1;
    j += b > 0xffff ? 2 : 1;
  }
  if (i < left.length) return 1;
  return j < right.length ? -1 : 0;
}

export interface WorkspaceFileEntry {
  path: string;
  objectId: string;
  contentHash: string;
  sizeBytes: number;
  mimeType: string;
  /** Absent means `FILE_MODE`. */
  mode?: number;
}

export type WorkspaceFiles = ReadonlyMap<string, WorkspaceFileEntry>;

export interface WorkspaceManifestView {
  workspaceId: string;
  revision: number;
  parentRevision: number | null;
  contentHash: string;
  /** Sorted by path (code point order). */
  files: WorkspaceFileEntry[];
  createdAt: number;
}

const MAX_OBJECT_ID_LENGTH = 512;

/**
 * An object id with no backslash, escape, control character or dot segment: it can only name itself once it is put
 * into a storage URL. A sealed id is `prefix/size/hash/inner`, so `/` itself is allowed.
 */
export const isPlainObjectId = (id: string): boolean =>
  id.length <= MAX_OBJECT_ID_LENGTH &&
  !/[\\%\p{Cc}]/u.test(id) &&
  !id.split('/').some((segment) => segment === '.' || segment === '..');

export function fileEntry(raw: WorkspaceFileEntry): WorkspaceFileEntry {
  if (raw.objectId === '' || raw.contentHash === '' || raw.sizeBytes < 0) {
    throw invalid('Object reference is incomplete');
  }
  // SEC-FS-02: an object id is put into a storage URL; one that carries a path could name another object.
  if (!isPlainObjectId(raw.objectId)) {
    throw invalid('Object reference is not a plain id');
  }
  const mode = raw.mode ?? FILE_MODE;
  if (!validFileMode(mode)) {
    throw invalid(
      "Workspace file mode must be a regular file's permissions or a symbolic link",
    );
  }
  return {
    path: normalizeWorkspacePath(raw.path),
    objectId: raw.objectId,
    contentHash: raw.contentHash,
    sizeBytes: raw.sizeBytes,
    mimeType: raw.mimeType,
    ...(mode === FILE_MODE ? {} : { mode }),
  };
}

export const sortedFiles = (files: WorkspaceFiles): WorkspaceFileEntry[] =>
  [...files.values()].sort((a, b) => compareCodePoints(a.path, b.path));

/** Python: `workspace_manifest_content_hash`; independent of object ids and revision. */
export function manifestContentHash(files: WorkspaceFiles): string {
  const canonical = sortedFiles(files).map((entry) => ({
    path: entry.path,
    contentHash: entry.contentHash,
    sizeBytes: entry.sizeBytes,
    mimeType: entry.mimeType,
    ...((entry.mode ?? FILE_MODE) === FILE_MODE ? {} : { mode: entry.mode }),
  }));
  const digest = createHash('sha256')
    .update(JSON.stringify(canonical), 'utf8')
    .digest('hex');
  return `sha256:${digest}`;
}

const sameContent = (
  left: WorkspaceFileEntry | undefined,
  right: WorkspaceFileEntry | undefined,
): boolean =>
  left === undefined || right === undefined
    ? left === right
    : left.contentHash === right.contentHash &&
      left.sizeBytes === right.sizeBytes &&
      left.mimeType === right.mimeType &&
      (left.mode ?? FILE_MODE) === (right.mode ?? FILE_MODE);

export interface WorkspaceChanges {
  upserts: WorkspaceFileEntry[];
  deletes: string[];
}

/** Canonical, with no path both written and deleted (Python: `ChangeSet`). */
export function validateChanges(changes: WorkspaceChanges): WorkspaceChanges {
  const upserts = new Map<string, WorkspaceFileEntry>();
  for (const raw of changes.upserts) {
    const entry = fileEntry(raw);
    if (entry.path !== raw.path) {
      throw invalid('Workspace file paths must be canonical');
    }
    upserts.set(entry.path, entry);
  }
  const deletes = new Set<string>();
  for (const raw of changes.deletes) {
    const path = normalizeWorkspacePath(raw);
    if (path !== raw) throw invalid('Deleted paths must be canonical');
    if (upserts.has(path)) {
      throw invalid('A path cannot be both written and deleted');
    }
    deletes.add(path);
  }
  return { upserts: [...upserts.values()], deletes: [...deletes] };
}

export function applyChanges(
  files: WorkspaceFiles,
  changes: WorkspaceChanges,
): Map<string, WorkspaceFileEntry> {
  const missing = changes.deletes.filter((path) => !files.has(path));
  if (missing.length > 0) {
    throw invalid(
      `Cannot delete ${String(missing.length)} path(s) not in the workspace`,
    );
  }
  const result = new Map(files);
  for (const path of changes.deletes) result.delete(path);
  for (const entry of changes.upserts) result.set(entry.path, entry);
  if (result.size > MAX_WORKSPACE_FILES) {
    throw new WorkspaceError(
      'WORKSPACE_TOO_LARGE',
      `A workspace holds at most ${String(MAX_WORKSPACE_FILES)} files`,
    );
  }
  return result;
}

/** How a commit changed one path (`workspace.committed` `kinds`; Python: `FileChangeKind`). */
export type FileChangeKind = 'added' | 'modified' | 'deleted';

/** How applying `changes` to `before` changes each of its paths (Python: `ChangeSet.kinds`). */
export function changeKinds(
  changes: WorkspaceChanges,
  before: WorkspaceFiles,
): Record<string, FileChangeKind> {
  const kinds: Record<string, FileChangeKind> = {};
  for (const path of changes.deletes) kinds[path] = 'deleted';
  for (const { path } of changes.upserts) {
    kinds[path] = before.has(path) ? 'modified' : 'added';
  }
  return kinds;
}

export const isEmptyChange = (changes: WorkspaceChanges): boolean =>
  changes.upserts.length === 0 && changes.deletes.length === 0;

/** Paths whose entry differs between `a` and `b` (content or MIME type). */
export function changedPaths(a: WorkspaceFiles, b: WorkspaceFiles): string[] {
  const paths: string[] = [];
  for (const [path, entry] of b) {
    if (!sameContent(a.get(path), entry)) paths.push(path);
  }
  for (const path of a.keys()) if (!b.has(path)) paths.push(path);
  return paths.sort(compareCodePoints);
}

export interface PathConflict {
  path: string;
  ours: WorkspaceFileEntry | null;
  theirs: WorkspaceFileEntry | null;
}

/** A conflict as P6.4 names it (Python's `ConflictKind`). */
export type ConflictKind = 'both_changed' | 'target_deleted' | 'fork_deleted';

export const conflictKind = (conflict: PathConflict): ConflictKind =>
  conflict.ours === null
    ? 'target_deleted'
    : conflict.theirs === null
      ? 'fork_deleted'
      : 'both_changed';

export interface MergePlan {
  changes: WorkspaceChanges;
  conflicts: PathConflict[];
  repositoryConflicts: string[];
}

const repositories = (paths: string[]): Set<string> =>
  new Set(
    paths.map(repositoryOf).filter((repo): repo is string => repo !== null),
  );

/**
 * Three-way merge of `theirs` into `ours` (Python: `plan_merge`). A
 * repository both sides touched is reported, never merged path by path.
 */
export function planMerge(
  base: WorkspaceFiles,
  ours: WorkspaceFiles,
  theirs: WorkspaceFiles,
): MergePlan {
  const theirPaths = changedPaths(base, theirs);
  const ourRepositories = repositories(changedPaths(base, ours));
  const blocked = new Set(
    [...repositories(theirPaths)].filter((repo) => ourRepositories.has(repo)),
  );
  const upserts: WorkspaceFileEntry[] = [];
  const deletes: string[] = [];
  const conflicts: PathConflict[] = [];
  for (const path of theirPaths) {
    const repo = repositoryOf(path);
    if (repo !== null && blocked.has(repo)) continue;
    const mine = ours.get(path);
    const yours = theirs.get(path);
    if (sameContent(mine, yours)) continue;
    if (!sameContent(mine, base.get(path))) {
      conflicts.push({ path, ours: mine ?? null, theirs: yours ?? null });
    } else if (yours === undefined) {
      deletes.push(path);
    } else {
      upserts.push(yours);
    }
  }
  return {
    changes: { upserts, deletes },
    conflicts,
    repositoryConflicts: [...blocked].sort(compareCodePoints),
  };
}

/**
 * Engine plug-in folders and project configs (Python: `harness_core.workspace.sync.rules.is_engine_config`):
 * never materialised into a sandbox, so a person's upload may not write one (SLASH3 §3.4).
 */
const ENGINE_CONFIG_DIRS: ReadonlySet<string> = new Set([
  '.opencode',
  '.claude',
  '.codex',
]);
const ENGINE_CONFIG_FILES: ReadonlySet<string> = new Set([
  'opencode.json',
  'opencode.jsonc',
  '.mcp.json',
]);

export function isEngineConfigPath(path: string): boolean {
  const parts = path.split('/');
  return (
    ENGINE_CONFIG_FILES.has(parts[parts.length - 1] ?? '') ||
    parts.some((part) => ENGINE_CONFIG_DIRS.has(part))
  );
}

/** One workspace per session (`ws:{sessionId}`); forks and children extend it. */
export const sessionWorkspaceId = (sessionId: string): string =>
  `${WORKSPACE_ID_PREFIX}${sessionId}`;

export const workspaceInSession = (
  workspaceId: string,
  sessionId: string,
): boolean => {
  const root = sessionWorkspaceId(sessionId);
  return workspaceId === root || workspaceId.startsWith(`${root}:`);
};

const FORK = ':fork:';
const PRINCIPAL = ':p:';

/** Whose runs a workspace serves: a run's actor, and whether they are a collaborator in its session. */
export interface WorkspacePrincipal {
  sessionId: string;
  actorId: string;
  sessionRole?: TurnRole;
}

/**
 * The workspace one principal's root runs of a session write (OWD-16;
 * Python: `principal_workspace_id`): the session's own for its owner, and one
 * of their own beside it for a collaborator, so files fetched under two
 * people's access never mix.
 */
export const principalWorkspaceId = (principal: WorkspacePrincipal): string =>
  principal.sessionRole === 'collaborator'
    ? `${sessionWorkspaceId(principal.sessionId)}${PRINCIPAL}${principal.actorId}`
    : sessionWorkspaceId(principal.sessionId);

/**
 * Whether the workspace is the principal's own or one under it, such as a
 * fork (Python: `workspace_of_principal`). The owner's tree is the session's,
 * less every collaborator's.
 */
export const workspaceOfPrincipal = (
  principal: WorkspacePrincipal,
  workspaceId: string,
): boolean => {
  const root = principalWorkspaceId(principal);
  if (workspaceId !== root && !workspaceId.startsWith(`${root}:`)) return false;
  return (
    principal.sessionRole === 'collaborator' ||
    !workspaceId.startsWith(`${root}${PRINCIPAL}`)
  );
};

/** The principal workspace a workspace or any of its forks belongs to (Python: `fork_root`). */
const forkRoot = (workspaceId: string): string =>
  workspaceId.split(FORK, 1)[0] ?? workspaceId;

/** A child run's fork of `origin` (Python: `child_fork_of`): flat under its principal's workspace whatever the depth. */
export const childForkOf = (origin: string, childRunId: string): string =>
  `${forkRoot(origin)}${FORK}${childRunId}`;

/** The fork of a child of the session owner's runs (Python: `child_fork_id`). */
export const childForkId = (sessionId: string, childRunId: string): string =>
  childForkOf(sessionWorkspaceId(sessionId), childRunId);

/**
 * What a run may write (P6.4): a root run any workspace of its principal; a
 * child neither its principal's own workspace nor another run's fork, so it
 * writes its files only on its own fork. Which principal's workspaces a run
 * reaches at all is `workspaceOfPrincipal`'s (OWD-16).
 */
export function workspaceWritableBy(
  run: WorkspacePrincipal & { runId: string; parent?: unknown },
  workspaceId: string,
): boolean {
  if (run.parent === undefined || run.parent === null) return true;
  const root = principalWorkspaceId(run);
  if (workspaceId === root) return false;
  return (
    !workspaceId.startsWith(`${root}${FORK}`) ||
    workspaceId === childForkOf(root, run.runId)
  );
}

/** The cipher binds org, key and purpose; this binds the manifest's identity. */
export const manifestSealContext = (
  workspaceId: string,
  revision: number,
): SealContext => ({ workspaceId, revision });

export const workspaceKeySealContext = (
  sessionId: string,
  keyId: string,
): SealContext => ({ sessionId, keyId });
