import * as fs from 'fs';
import * as fsp from 'fs/promises';
import * as path from 'path';

/** Kept under socket.io's 1MB default maxHttpBufferSize on both ends. */
const DEFAULT_CHUNK_BYTES = 256 * 1024;
const MAX_CHUNK_BYTES = 512 * 1024;
/** Matches the relay's ceiling; refuse early rather than after three retries. */
const DEFAULT_MAX_BYTES = 100 * 1024 * 1024;
const MIME_BY_EXT = new Map<string, string>(Object.entries({
  txt: 'text/plain',
  log: 'text/plain',
  md: 'text/markdown',
  json: 'application/json',
  yaml: 'application/x-yaml',
  yml: 'application/x-yaml',
  csv: 'text/csv',
  tsv: 'text/tab-separated-values',
  html: 'text/html',
  htm: 'text/html',
  css: 'text/css',
  js: 'application/javascript',
  pdf: 'application/pdf',
  doc: 'application/msword',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  xls: 'application/vnd.ms-excel',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  ppt: 'application/vnd.ms-powerpoint',
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  gif: 'image/gif',
  webp: 'image/webp',
  svg: 'image/svg+xml',
  mp3: 'audio/mpeg',
  wav: 'audio/wav',
  mp4: 'video/mp4',
  mov: 'video/quicktime',
  zip: 'application/zip',
}));

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

export function mimeTypeForPath(relPath: string): string {
  const ext = path.extname(String(relPath || '')).replace(/^\./, '').toLowerCase();
  return MIME_BY_EXT.get(ext) || 'application/octet-stream';
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
  const candidate = path.resolve(root, String(relPath || ''));
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
      // The file is gone or unreadable — terminal for this record, so the
      // indexing consumer stops retrying instead of burning its budget.
      return failure(
        requestId,
        'FILE_UNREADABLE',
        error instanceof Error ? error.message : String(error),
        false,
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
      void this.streamFile(absPath, stats.size, chunkBytes, emitChunk, abort);
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
    expectedSize: number,
    chunkBytes: number,
    emitChunk: ChunkEmitter,
    abort: AbortEmitter,
  ): Promise<void> {
    let handle: fsp.FileHandle | null = null;
    let seq = 0;
    let sent = 0;
    try {
      handle = await fsp.open(absPath, 'r');
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
        retryable: false,
      });
    } finally {
      if (handle) await handle.close().catch(() => { /* ignore */ });
    }
  }
}
