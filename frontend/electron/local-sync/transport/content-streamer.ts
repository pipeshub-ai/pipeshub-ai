import * as fs from 'fs';
import * as fsp from 'fs/promises';
import * as path from 'path';
import { mimeTypeForPath } from '../mime';

/**
 * Node has no O_NOFOLLOW on Windows, where it is undefined; the `?? 0`
 * degrades that platform to a plain read-only open instead of throwing.
 */
const NO_FOLLOW_READ_FLAGS = fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW ?? 0);

/** Kept under socket.io's 1MB default maxHttpBufferSize on both ends. */
const DEFAULT_CHUNK_BYTES = 256 * 1024;
const MAX_CHUNK_BYTES = 512 * 1024;
/** Matches the relay's ceiling; refuse early rather than after three retries. */
const DEFAULT_MAX_BYTES = 100 * 1024 * 1024;

export interface ContentFetchRequest {
  requestId: string;
  connectorId: string;
  relPath: string;
  externalRecordId?: string;
  sha256?: string | null;
  timeoutMs?: number;
  maxBytes?: number;
  chunkBytes?: number;
}

export type ContentStreamAck =
  | { ok: true; requestId: string; size: number; mimeType: string }
  | {
      ok: false;
      requestId?: string;
      error: { code: string; message: string; retryable: boolean };
    };

export type ChunkEmitter = (seq: number, data: Buffer, final: boolean) => void;
export type AbortEmitter = (error: {
  code: string;
  message: string;
  retryable: boolean;
}) => void;

export interface ContentStreamerDeps {
  /** Configured sync root for this connector, or null when it is unknown here. */
  getRootPath: (connectorId: string) => string | null;
  log?: (message: string) => void;
}

/**
 * Resolve `relPath` to an absolute path guaranteed to sit inside `rootPath`.
 *
 * The server supplies relPath, so treating it as trusted is a path-traversal
 * hole. `realpath` is applied to both sides so a symlink inside the root
 * cannot point out of it.
 */
export function resolveInsideRoot(rootPath: string, relPath: string): string | null {
  const rawRoot = path.resolve(String(rootPath || ''));
  if (!rawRoot) return null;
  let root: string;
  try {
    root = fs.realpathSync.native(rawRoot);
  } catch {
    root = rawRoot;
  }
  let candidate = path.resolve(root, String(relPath || ''));
  if (!fs.existsSync(candidate)) {
    candidate = resolveNormalizationVariant(root, String(relPath || '')) ?? candidate;
  }
  let resolved: string;
  try {
    resolved = fs.realpathSync.native(candidate);
  } catch {
    resolved = candidate;
  }
  const relative = path.relative(root, resolved);
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) {
    return null;
  }
  return resolved;
}

/**
 * Whether a read error means the file is gone (stop asking) or only that it
 * cannot be read right now: no permission yet, locked by another program
 * (EBUSY on Windows), too many open files. The indexer gives up on the first
 * kind and retries the second, so guessing "gone" loses the file for good.
 */
function errorIsTransient(error: unknown): boolean {
  const code = (error as NodeJS.ErrnoException | undefined)?.code;
  return code !== 'ENOENT' && code !== 'ENOTDIR' && code !== 'ELOOP';
}

/**
 * The path as it exists on disk. The server sends paths in NFC (that is how
 * they are keyed), but a filesystem that keeps names as written (ext4, NTFS)
 * may hold the NFD form a Mac produced; matching each segment by its NFC form
 * finds that file instead of answering "not found".
 */
function resolveNormalizationVariant(root: string, relPath: string): string | null {
  let current = root;
  for (const segment of String(relPath || '').split(/[\\/]+/).filter(Boolean)) {
    const direct = path.join(current, segment);
    if (fs.existsSync(direct)) {
      current = direct;
      continue;
    }
    let names: string[];
    try {
      names = fs.readdirSync(current);
    } catch {
      return null;
    }
    const wanted = segment.normalize('NFC');
    const match = names.find((name) => name.normalize('NFC') === wanted);
    if (!match) return null;
    current = path.join(current, match);
  }
  return current;
}

function failure(
  requestId: string,
  code: string,
  message: string,
  retryable: boolean,
): ContentStreamAck {
  return { ok: false, requestId, error: { code, message, retryable } };
}

/**
 * Answer one `localfs:content:fetch`: ack the metadata, then push the bytes as
 * ordered `localfs:content:chunk` frames. The ack resolves before the first
 * frame, so the server can size its buffer and reject an oversize file without
 * waiting out its timeout.
 */
export class ContentStreamer {
  private readonly log: (message: string) => void;

  constructor(private readonly deps: ContentStreamerDeps) {
    this.log = deps.log || ((msg) => console.log('[content-streamer]', msg));
  }

  async serve(
    request: ContentFetchRequest,
    emitChunk: ChunkEmitter,
    abort: AbortEmitter,
  ): Promise<ContentStreamAck> {
    const requestId = String(request?.requestId || '');
    if (!requestId) {
      return failure('', 'BAD_REQUEST', 'content fetch had no requestId', false);
    }
    const rootPath = this.deps.getRootPath(request.connectorId);
    if (!rootPath) {
      return failure(
        requestId,
        'CONFIG_MISMATCH',
        `connector ${request.connectorId} is not configured on this machine`,
        false,
      );
    }
    const absPath = resolveInsideRoot(rootPath, request.relPath);
    if (!absPath) {
      this.log(`refused path outside sync root: ${request.relPath}`);
      return failure(
        requestId,
        'PATH_OUTSIDE_ROOT',
        `${request.relPath} does not resolve inside the sync root`,
        false,
      );
    }

    let stats: fs.Stats;
    try {
      stats = await fsp.stat(absPath);
    } catch (error) {
      // A file that is gone is terminal for this record, so the indexing
      // consumer stops retrying; one that cannot be read right now is not.
      return failure(
        requestId,
        'FILE_UNREADABLE',
        error instanceof Error ? error.message : String(error),
        errorIsTransient(error),
      );
    }
    if (!stats.isFile()) {
      return failure(requestId, 'NOT_A_FILE', `${request.relPath} is not a file`, false);
    }

    const maxBytes = Number(request.maxBytes) > 0 ? Number(request.maxBytes) : DEFAULT_MAX_BYTES;
    if (stats.size > maxBytes) {
      return failure(
        requestId,
        'CONTENT_TOO_LARGE',
        `${request.relPath} is ${stats.size} bytes, over the ${maxBytes} limit`,
        false,
      );
    }

    const chunkBytes = Math.min(
      MAX_CHUNK_BYTES,
      Number(request.chunkBytes) > 0 ? Number(request.chunkBytes) : DEFAULT_CHUNK_BYTES,
    );

    // Streaming starts after the ack returns so the frames cannot outrun it.
    setImmediate(() => {
      void this.streamFile(absPath, stats, chunkBytes, emitChunk, abort);
    });

    return {
      ok: true,
      requestId,
      size: stats.size,
      mimeType: mimeTypeForPath(request.relPath),
    };
  }

  private async streamFile(
    absPath: string,
    expectedStat: fs.Stats,
    chunkBytes: number,
    emitChunk: ChunkEmitter,
    abort: AbortEmitter,
  ): Promise<void> {
    let handle: fsp.FileHandle | null = null;
    let seq = 0;
    let sent = 0;
    const expectedSize = expectedStat.size;
    try {
      // Re-opening `absPath` by string here is itself a second, separate
      // filesystem lookup — a symlink swapped in after resolveInsideRoot's
      // check (and before this deferred open runs) would otherwise be
      // followed straight out of the sync root. O_NOFOLLOW refuses that if
      // the final path segment is now a symlink; the inode check below
      // binds every subsequent read to the exact file identity that was
      // validated, catching a same-named regular-file swap too.
      // (`dev` is left out of the comparison: Node reports it inconsistently
      // between a path-based stat and an fstat on the same file on Windows.)
      handle = await fsp.open(absPath, NO_FOLLOW_READ_FLAGS);
      const openedStat = await handle.stat();
      if (!openedStat.isFile() || openedStat.ino !== expectedStat.ino) {
        throw new Error(`${absPath} changed identity between validation and open`);
      }
      if (expectedSize === 0) {
        // The loop below only emits for bytes read, so an empty file would
        // never send the final frame the server waits for.
        emitChunk(0, Buffer.alloc(0), true);
        return;
      }
      const buffer = Buffer.allocUnsafe(chunkBytes);
      for (;;) {
        const { bytesRead } = await handle.read(buffer, 0, chunkBytes, sent);
        if (bytesRead === 0) break;
        sent += bytesRead;
        // Copy: the shared read buffer is reused on the next iteration and
        // socket.io serializes asynchronously.
        emitChunk(seq, Buffer.from(buffer.subarray(0, bytesRead)), sent >= expectedSize);
        seq += 1;
        if (sent >= expectedSize) return;
      }
      // The file shrank under us; the server would otherwise wait for a final
      // frame that never comes.
      abort({
        code: 'CONTENT_SIZE_MISMATCH',
        message: `${absPath} shrank from ${expectedSize} to ${sent} bytes mid-transfer`,
        retryable: true,
      });
    } catch (error) {
      abort({
        code: 'READ_FAILED',
        message: error instanceof Error ? error.message : String(error),
        retryable: errorIsTransient(error),
      });
    } finally {
      if (handle) await handle.close().catch(() => { /* ignore */ });
    }
  }
}
