import { HarnessNotFoundError } from '../domain/errors';
import {
  changedPaths,
  isEngineConfigPath,
  normalizeWorkspacePath,
  WorkspaceError,
  WorkspaceFileEntry,
  WorkspaceManifestView,
} from '../domain/workspace';
import { ZipEntry } from '../domain/workspace-zip';
import {
  decodeStorageRecord,
  isStorageRecordPath,
  STORAGE_RECORD_PATH,
  WorkspaceReference,
} from '../domain/storage-record';
import {
  checkName,
  checkPortablePath,
  UnsafeNameError,
  UnsafeNameReason,
} from '../domain/safe-names';
import {
  WORKSPACE_DOWNLOADED_KIND,
  WorkspaceDownloadAudit,
} from '../domain/workspace-download-audit';
import {
  DIFF_MAX_FILE_BYTES,
  textOf,
  unifiedDiff,
  WorkspaceDiffFile,
} from '../domain/workspace-diff';
import {
  ChatSessionRepository,
  SessionExecutionScope,
} from '../ports/chat-session-repository';
import { ObjectRead, ObjectWrite } from './workspace-object-resealer';
import {
  RestoredWorkspaceVersion,
  UploadedWorkspaceFiles,
  WorkspaceService,
  WorkspaceVersions,
} from './workspace-service';

export const MAX_VERSIONS_PAGE = 100;
export const DEFAULT_VERSIONS_PAGE = 50;
/** Paths a diff lists; the rest are counted in `omitted`. */
export const DIFF_MAX_FILES = 200;
/** Files a diff reads to show their text; later ones are listed without it. */
export const DIFF_MAX_READ_FILES = 50;
/** Diff text one answer carries, over all its files. */
export const DIFF_MAX_TOTAL_CHARS = 200_000;

/** What `/diff` shows (SLASH2 C3): the session workspace's changes over the chat, or over its last turn. */
export interface WorkspaceDiff {
  workspaceId: string;
  head: number;
  /** The revision the changes are counted from (0: before the first file). */
  from: number;
  to: number;
  files: WorkspaceDiffFile[];
  additions: number;
  deletions: number;
  /** Changed paths past `DIFF_MAX_FILES`, not listed. */
  omitted: number;
}

export type WorkspaceDiffScope = 'chat' | 'turn';

/** Opens a session's objects; absent where Node has no object store (the diff then lists paths only). */
export type SessionObjectReader = (
  orgId: string,
  sessionId: string,
) => Promise<ObjectRead>;

/** Seals and stores a person's files with the session's key; absent where Node has no object store. */
export type SessionObjectWriter = (
  orgId: string,
  sessionId: string,
) => Promise<ObjectWrite>;

const filesOf = (
  view: WorkspaceManifestView,
): Map<string, WorkspaceFileEntry> =>
  new Map(view.files.map((entry) => [entry.path, entry]));

/** Where `/upload` puts files when no folder is named (SD6). */
export const UPLOAD_DEFAULT_FOLDER = 'uploads';
/** Files one upload may carry. */
export const UPLOAD_MAX_FILES = 1_000;

/**
 * One uploaded file. Its bytes are read when it is written, one file at a time, so an upload of many large files
 * never sits in memory whole (SEC-C-08).
 */
export interface UploadedFileSource {
  path: string;
  mimeType: string;
  sizeBytes: number;
  read(): Promise<Buffer>;
}

/** A file already in memory, as an upload source. */
export const bufferedUploadFile = (
  path: string,
  bytes: Buffer,
  mimeType: string,
): UploadedFileSource => ({
  path,
  mimeType,
  sizeBytes: bytes.length,
  read: () => Promise.resolve(bytes),
});

export interface WorkspaceUpload {
  baseRevision: number;
  idempotencyKey: string;
  /** Under the workspace root; `uploads` when absent. */
  folder?: string;
  /** Each file's path under the folder (a dropped folder keeps its tree), its size and type, and its bytes. */
  files: UploadedFileSource[];
  /** The person confirmed replacing the files it names that already exist. */
  replace: boolean;
}

export interface WorkspaceUploadResult extends UploadedWorkspaceFiles {
  paths: string[];
  replaced: string[];
}

/** Entries `/files` lists; the rest are counted in `omitted`. */
export const FILES_MAX_LISTED = 5_000;
/** The sync's own bound on a workspace (Python `SyncLimits.max_archive_bytes`): a bigger zip is refused. */
export const ARCHIVE_MAX_BYTES = 2 * 1024 ** 3;

/** `/files`: a revision's files under a folder, as its owner sees them (SLASH3 §3.1). */
export interface WorkspaceFileList {
  workspaceId: string;
  revision: number;
  createdAt: number;
  files: Array<{
    path: string;
    sizeBytes: number;
    mode?: number;
    hidden?: true;
  }>;
  /** Over every file under the folder, listed or not. */
  totalBytes: number;
  omitted: number;
  /**
   * Fetched copies under the folder (ADR-RUN-01 D2): recorded by address and version, never saved as files, so they
   * are named on their own line and never listed among the files.
   */
  references?: WorkspaceReference[];
}

export interface WorkspaceFileContent {
  path: string;
  revision: number;
  sizeBytes: number;
  mode?: number;
  bytes: Buffer;
  /** Only the first `maxBytes` were asked for. */
  truncated: boolean;
}

export interface WorkspaceArchive {
  revision: number;
  files: number;
  bytes: number;
  entries: ZipEntry[];
}

export type { WorkspaceDownloadAudit };

/** Where a download is audited before a byte of it is sent (GAPS2-2: the org's audit chain). */
export type WorkspaceAuditSink = (row: WorkspaceDownloadAudit) => Promise<void>;

const pathNotFound = (path: string): WorkspaceError =>
  new WorkspaceError(
    'WORKSPACE_PATH_NOT_FOUND',
    `No file or folder at ${path}`,
  );

const UNSAFE_NAME_WHY: Record<UnsafeNameReason, string> = {
  empty: 'it is empty',
  too_long: 'it is too long',
  separator: 'it has a character that separates folders on some computers',
  traversal: 'it points outside its folder',
  nul: 'it has a character no file name may hold',
  control: 'it has a control character',
  encoded_separator: 'it hides a folder separator',
  drive: 'it names a drive',
  reserved: 'Windows keeps it for a device',
  trailing_dot_or_space: 'it ends with a dot or a space',
  not_a_path: 'it is not a plain path',
};

/** A person's upload path, every segment one plain name; refused naming the first segment that is not. */
const portable = (entry: WorkspaceFileEntry): boolean => {
  try {
    checkPortablePath(entry.path);
    return true;
  } catch {
    return false;
  }
};

function uploadPath(path: string): string {
  const segments = path.split('/');
  for (const segment of segments.length > 1
    ? segments.filter((s) => s !== '')
    : segments) {
    try {
      checkName(segment);
    } catch (error) {
      if (!(error instanceof UnsafeNameError)) throw error;
      throw new WorkspaceError(
        'WORKSPACE_INVALID_CHANGE',
        `The name "${segment.replace(/\p{Cc}/gu, '?')}" can't be used: ${UNSAFE_NAME_WHY[error.reason]}. Rename it and upload again.`,
      );
    }
  }
  return normalizeWorkspacePath(path);
}

/** The person's files of a revision: never the harness's own record of its references (ADR-RUN-01 D2). */
function personFiles(view: WorkspaceManifestView): WorkspaceFileEntry[] {
  return view.files.filter((entry) => !isStorageRecordPath(entry.path));
}

/** Whether `path` is `prefix`, inside the folder `prefix`, or anywhere when there is no prefix. */
function under(path: string, prefix: string | undefined): boolean {
  if (prefix === undefined || prefix === '') return true;
  // Entry paths are canonical (NFC), so a folder typed in another form must be too, or it names nothing.
  const canonical = normalizeWorkspacePath(prefix);
  return path === canonical || path.startsWith(`${canonical}/`);
}

/** A file at `prefix`, or the files in the folder `prefix`; every file when it is absent. */
function filesUnder(
  view: WorkspaceManifestView,
  prefix: string | undefined,
): WorkspaceFileEntry[] {
  return personFiles(view).filter((entry) => under(entry.path, prefix));
}

/**
 * The session workspace as its owner sees it in the chat (U3): the version
 * list, restoring an earlier version as a new one, and what changed (SLASH2
 * `/diff`, `/undo`). Only the session's owner: a shared viewer or anyone else
 * gets 404, as for the run routes.
 */
export class SessionWorkspaceService {
  constructor(
    private readonly sessions: Pick<ChatSessionRepository, 'findStreamAccess'>,
    private readonly workspaces: Pick<
      WorkspaceService,
      'versions' | 'restore' | 'sessionFiles' | 'lastTurn' | 'upload'
    >,
    private readonly objects?: SessionObjectReader,
    private readonly audit?: WorkspaceAuditSink,
    private readonly writer?: SessionObjectWriter,
  ) {}

  /**
   * A person's files into the session workspace (SLASH3 `/upload`, SD6): under `folder`, as one revision `/undo`
   * takes back. Paths are checked as a commit's are; an engine's plug-in path is refused, so an upload can never
   * configure an engine; replacing a file needs the person's confirm; the workspace keeps the sync's bounds.
   */
  async upload(
    scope: SessionExecutionScope,
    request: WorkspaceUpload,
  ): Promise<WorkspaceUploadResult> {
    await this.owner(scope);
    if (this.writer === undefined) {
      throw new Error('Session workspace objects are not configured');
    }
    if (request.files.length === 0 || request.files.length > UPLOAD_MAX_FILES) {
      throw new WorkspaceError(
        'WORKSPACE_INVALID_CHANGE',
        `An upload carries 1 to ${String(UPLOAD_MAX_FILES)} files`,
      );
    }
    // SEC-FS-02: every folder and file name a person sends is one plain name on every system.
    const folder = uploadPath(
      (request.folder ?? UPLOAD_DEFAULT_FOLDER).replace(/^\/+|\/+$/g, '') ||
        UPLOAD_DEFAULT_FOLDER,
    );
    const paths = request.files.map((file) =>
      uploadPath(`${folder}/${file.path}`),
    );
    const engineConfig = paths.filter(
      (path) => isEngineConfigPath(path) || isStorageRecordPath(path),
    );
    if (engineConfig.length > 0) {
      throw new WorkspaceError(
        'WORKSPACE_INVALID_CHANGE',
        `An upload cannot write engine configuration: ${engineConfig.slice(0, 5).join(', ')}`,
      );
    }
    if (new Set(paths).size !== paths.length) {
      throw new WorkspaceError(
        'WORKSPACE_INVALID_CHANGE',
        'An upload names the same path twice',
      );
    }
    const head = await this.workspaces.sessionFiles(scope);
    const existing = filesOf(head);
    const replaced = paths.filter((path) => existing.has(path));
    // On a moved head the store decides: a retry of this upload answers its revision, anything else conflicts.
    if (
      replaced.length > 0 &&
      !request.replace &&
      head.revision === request.baseRevision
    ) {
      throw new WorkspaceError(
        'WORKSPACE_PATH_EXISTS',
        `Replace ${String(replaced.length)} file(s)? ${replaced.slice(0, 5).join(', ')}`,
      );
    }
    const added = request.files.reduce((sum, file) => sum + file.sizeBytes, 0);
    const kept = head.files
      .filter((entry) => !replaced.includes(entry.path))
      .reduce((sum, entry) => sum + entry.sizeBytes, 0);
    if (kept + added > ARCHIVE_MAX_BYTES) {
      throw new WorkspaceError(
        'WORKSPACE_TOO_LARGE',
        'The workspace would be larger than 2 GiB',
      );
    }
    const write = await this.writer(scope.orgId, scope.sessionId);
    const files: WorkspaceFileEntry[] = [];
    for (const [index, file] of request.files.entries()) {
      files.push(
        await write(paths[index] as string, await file.read(), file.mimeType),
      );
    }
    const uploaded = await this.workspaces.upload({
      orgId: scope.orgId,
      sessionId: scope.sessionId,
      actorId: scope.actorId,
      baseRevision: request.baseRevision,
      idempotencyKey: request.idempotencyKey,
      files,
    });
    return { ...uploaded, paths, replaced };
  }

  async versions(
    scope: SessionExecutionScope,
    page: { limit?: number; before?: number },
  ): Promise<WorkspaceVersions> {
    await this.owner(scope);
    return this.workspaces.versions(scope, {
      limit: Math.min(page.limit ?? DEFAULT_VERSIONS_PAGE, MAX_VERSIONS_PAGE),
      ...(page.before !== undefined ? { before: page.before } : {}),
    });
  }

  async restore(
    scope: SessionExecutionScope,
    request: { revision: number; baseRevision: number; idempotencyKey: string },
  ): Promise<RestoredWorkspaceVersion> {
    await this.owner(scope);
    return this.workspaces.restore({
      orgId: scope.orgId,
      sessionId: scope.sessionId,
      actorId: scope.actorId,
      ...request,
    });
  }

  /**
   * The files the chat's workspace changed since it began (`chat`), or that its
   * latest writing turn changed (`turn`: what `/undo` restores, from `from`).
   * A session whose key was destroyed answers WORKSPACE_KEY_DESTROYED (410).
   */
  async diff(
    scope: SessionExecutionScope,
    which: WorkspaceDiffScope,
  ): Promise<WorkspaceDiff> {
    await this.owner(scope);
    const head = await this.workspaces.sessionFiles(scope);
    const range =
      which === 'turn'
        ? await this.workspaces.lastTurn(scope)
        : { from: 0, to: head.revision };
    const base = {
      workspaceId: head.workspaceId,
      head: head.revision,
    };
    if (range === null || range.from === range.to) {
      return {
        ...base,
        from: head.revision,
        to: head.revision,
        files: [],
        additions: 0,
        deletions: 0,
        omitted: 0,
      };
    }
    const [before, after] = await Promise.all([
      this.workspaces.sessionFiles(scope, range.from),
      range.to === head.revision
        ? head
        : this.workspaces.sessionFiles(scope, range.to),
    ]);
    const a = filesOf(before);
    const b = filesOf(after);
    const paths = changedPaths(a, b).filter(
      (path) => !isStorageRecordPath(path),
    );
    const listed = paths.slice(0, DIFF_MAX_FILES);
    const read =
      this.objects !== undefined && listed.length > 0
        ? await this.objects(scope.orgId, scope.sessionId)
        : null;
    const files: WorkspaceDiffFile[] = [];
    let chars = 0;
    for (const path of listed) {
      const was = a.get(path);
      const now = b.get(path);
      const file: WorkspaceDiffFile = {
        path,
        change:
          was === undefined
            ? 'added'
            : now === undefined
              ? 'deleted'
              : 'modified',
        sizeBytes: (now ?? was)?.sizeBytes ?? 0,
        additions: 0,
        deletions: 0,
      };
      if (
        read !== null &&
        files.length < DIFF_MAX_READ_FILES &&
        chars < DIFF_MAX_TOTAL_CHARS
      ) {
        Object.assign(file, await this.fileDiff(read, path, was, now));
        chars += file.diff?.length ?? 0;
      }
      files.push(file);
    }
    return {
      ...base,
      from: range.from,
      to: range.to,
      files,
      additions: files.reduce((n, f) => n + f.additions, 0),
      deletions: files.reduce((n, f) => n + f.deletions, 0),
      omitted: paths.length - listed.length,
    };
  }

  /**
   * The files of a revision (default: the head) under `prefix`, for `/files` (SLASH3 §3.1): sizes and modes only,
   * never an object. Engine plug-in paths are listed with `hidden`, since engines never see them.
   */
  async files(
    scope: SessionExecutionScope,
    query: { revision?: number; prefix?: string },
  ): Promise<WorkspaceFileList> {
    await this.owner(scope);
    const view = await this.workspaces.sessionFiles(scope, query.revision);
    const found = filesUnder(view, query.prefix);
    const references = (await this.references(scope, view)).filter((ref) =>
      under(ref.path, query.prefix),
    );
    if (
      query.prefix &&
      found.length === 0 &&
      references.length === 0 &&
      view.revision > 0
    ) {
      throw pathNotFound(query.prefix);
    }
    return {
      workspaceId: view.workspaceId,
      revision: view.revision,
      createdAt: view.createdAt,
      files: found.slice(0, FILES_MAX_LISTED).map((entry) => ({
        path: entry.path,
        sizeBytes: entry.sizeBytes,
        ...(entry.mode !== undefined ? { mode: entry.mode } : {}),
        ...(isEngineConfigPath(entry.path) ? { hidden: true } : {}),
      })),
      totalBytes: found.reduce((sum, entry) => sum + entry.sizeBytes, 0),
      omitted: Math.max(0, found.length - FILES_MAX_LISTED),
      ...(references.length > 0 ? { references } : {}),
    };
  }

  /** The fetched copies a revision records; none when Node cannot read the session's objects or the record. */
  private async references(
    scope: SessionExecutionScope,
    view: WorkspaceManifestView,
  ): Promise<WorkspaceReference[]> {
    const record = view.files.find(
      (entry) => entry.path === STORAGE_RECORD_PATH,
    );
    if (record === undefined || this.objects === undefined) return [];
    try {
      const read = await this.objects(scope.orgId, scope.sessionId);
      return decodeStorageRecord(await read(record));
    } catch (error) {
      // A destroyed key is the whole answer (410), as for the files themselves; an unreadable record names none.
      if (error instanceof WorkspaceError) throw error;
      return [];
    }
  }

  /**
   * One file's bytes at a revision (`/open`, `/download <file>`), read with the session key inside Node; at most
   * `maxBytes` of them for a preview, with the full size alongside.
   */
  async file(
    scope: SessionExecutionScope,
    query: {
      path: string;
      revision?: number;
      maxBytes?: number;
      download?: boolean;
    },
  ): Promise<WorkspaceFileContent> {
    await this.owner(scope);
    const path = normalizeWorkspacePath(query.path);
    const view = await this.workspaces.sessionFiles(scope, query.revision);
    const entry = personFiles(view).find((f) => f.path === path);
    if (entry === undefined || this.objects === undefined) {
      throw pathNotFound(query.path);
    }
    const read = await this.objects(scope.orgId, scope.sessionId);
    const bytes = await read(entry);
    if (query.download === true) {
      await this.audit?.({
        type: WORKSPACE_DOWNLOADED_KIND,
        orgId: scope.orgId,
        actorId: scope.actorId,
        sessionId: scope.sessionId,
        revision: view.revision,
        target: 'file',
        path: entry.path,
        files: 1,
        bytes: entry.sizeBytes,
      });
    }
    const cut = query.maxBytes !== undefined && bytes.length > query.maxBytes;
    return {
      path: entry.path,
      revision: view.revision,
      sizeBytes: entry.sizeBytes,
      ...(entry.mode !== undefined ? { mode: entry.mode } : {}),
      bytes: cut ? bytes.subarray(0, query.maxBytes) : bytes,
      truncated: cut,
    };
  }

  /**
   * The files of a revision under `prefix` as zip entries (`/download`, SLASH3 §3.3), each read only when the
   * writer reaches it. Past `ARCHIVE_MAX_BYTES` it is refused before a byte is sent. The download is audited.
   */
  async archive(
    scope: SessionExecutionScope,
    query: { revision?: number; prefix?: string },
  ): Promise<WorkspaceArchive> {
    await this.owner(scope);
    const view = await this.workspaces.sessionFiles(scope, query.revision);
    // SEC-FS-02: a path saved before the portable rules, that a reader on another system would place outside the
    // folder it unpacks into, stays out of the zip.
    const under = filesUnder(view, query.prefix).filter(portable);
    if (under.length === 0) throw pathNotFound(query.prefix ?? '');
    const bytes = under.reduce((sum, entry) => sum + entry.sizeBytes, 0);
    if (bytes > ARCHIVE_MAX_BYTES) {
      throw new WorkspaceError(
        'WORKSPACE_TOO_LARGE',
        'The files are larger than 2 GiB; download a folder instead',
      );
    }
    if (this.objects === undefined) throw pathNotFound(query.prefix ?? '');
    const read = await this.objects(scope.orgId, scope.sessionId);
    await this.audit?.({
      type: WORKSPACE_DOWNLOADED_KIND,
      orgId: scope.orgId,
      actorId: scope.actorId,
      sessionId: scope.sessionId,
      revision: view.revision,
      target: 'archive',
      ...(query.prefix !== undefined && query.prefix !== ''
        ? { path: query.prefix }
        : {}),
      files: under.length,
      bytes,
    });
    return {
      revision: view.revision,
      files: under.length,
      bytes,
      entries: under.map((entry) => ({
        path: entry.path,
        ...(entry.mode !== undefined ? { mode: entry.mode } : {}),
        read: () => read(entry),
      })),
    };
  }

  private async fileDiff(
    read: ObjectRead,
    path: string,
    was: WorkspaceFileEntry | undefined,
    now: WorkspaceFileEntry | undefined,
  ): Promise<Partial<WorkspaceDiffFile>> {
    if (
      (was?.sizeBytes ?? 0) > DIFF_MAX_FILE_BYTES ||
      (now?.sizeBytes ?? 0) > DIFF_MAX_FILE_BYTES
    ) {
      return { tooLarge: true };
    }
    let texts: Array<string | null>;
    try {
      const bytes = await Promise.all(
        [was, now].map((e) =>
          e === undefined ? Promise.resolve(null) : read(e),
        ),
      );
      texts = bytes.map((b) => (b === null ? '' : textOf(b)));
    } catch (error) {
      // A destroyed key is the whole answer (410); one unreadable object only loses its own text.
      if (error instanceof WorkspaceError) throw error;
      return {};
    }
    if (texts.some((text) => text === null)) return { binary: true };
    const { diff, additions, deletions, truncated } = unifiedDiff(
      path,
      was === undefined ? null : (texts[0] as string),
      now === undefined ? null : (texts[1] as string),
    );
    return {
      diff,
      additions,
      deletions,
      ...(truncated ? { diffTruncated: true } : {}),
    };
  }

  private async owner(scope: SessionExecutionScope): Promise<void> {
    const access = await this.sessions.findStreamAccess(scope);
    if (access?.audience !== 'owner') throw new HarnessNotFoundError();
  }
}
