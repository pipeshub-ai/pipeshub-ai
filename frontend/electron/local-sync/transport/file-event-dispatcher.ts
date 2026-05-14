import * as crypto from 'crypto';
import { createReadStream } from 'fs';
import * as nodeFs from 'fs';
import * as fs from 'fs/promises';
import * as path from 'path';
import type { WatchEvent } from '../watcher/replay-event-expander';

type FsWithOpenBlob = typeof nodeFs & {
  openAsBlob?: (path: string, options?: { type?: string }) => Promise<Blob>;
};

const openAsBlob = (nodeFs as FsWithOpenBlob).openAsBlob?.bind(nodeFs);

const MAX_ATTEMPTS = 5;
const BASE_BACKOFF_MS = 500;
const REQUEST_TIMEOUT_MS = 30000;
const CONTENT_EVENT_TYPES = new Set(['CREATED', 'MODIFIED', 'RENAMED', 'MOVED']);
const MIME_BY_EXT = new Map<string, string>(Object.entries({
  txt: 'text/plain',
  log: 'text/plain',
  md: 'text/markdown',
  json: 'application/json',
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

export enum HttpStatusCode {
  Unauthorized = 401,
  TooManyRequests = 429,
  InternalServerError = 500,
}
const SERVER_ERROR_STATUS_EXCLUSIVE_UPPER_BOUND = 600;

export interface DispatchFileEventBatchArgs {
  apiBaseUrl: string;
  accessToken: string;
  connectorId: string;
  batchId: string;
  timestamp?: number;
  events: WatchEvent[];
  resetBeforeApply?: boolean;
  rootPath?: string | null;
  refreshAccessToken?: () => Promise<string | null>;
}

function trimTrailingSlash(value: unknown): string {
  return String(value || '').replace(/\/$/, '');
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function computeRetryAfterMs(response: Response): number | null {
  const header = response.headers && response.headers.get && response.headers.get('retry-after');
  if (!header) return null;
  const asSeconds = Number(header);
  if (Number.isFinite(asSeconds) && asSeconds >= 0) return Math.min(asSeconds * 1000, 60000);
  const when = Date.parse(header);
  if (!Number.isNaN(when)) return Math.max(0, Math.min(when - Date.now(), 60000));
  return null;
}

function isServerErrorStatus(status: number): boolean {
  return status >= HttpStatusCode.InternalServerError && status < SERVER_ERROR_STATUS_EXCLUSIVE_UPPER_BOUND;
}

async function postWithTimeout(url: string, init: RequestInit): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

function eventNeedsContent(event: WatchEvent): boolean {
  return !!event
    && !event.isDirectory
    && CONTENT_EVENT_TYPES.has(String(event.type || '').toUpperCase());
}

function mimeTypeForPath(relPath: string): string {
  const ext = path.extname(String(relPath || '')).replace(/^\./, '').toLowerCase();
  return MIME_BY_EXT.get(ext) || 'application/octet-stream';
}

function resolveEventFilePath(rootPath: string, relPath: string): string {
  const root = path.resolve(String(rootPath || ''));
  const candidate = path.resolve(root, String(relPath || ''));
  const relative = path.relative(root, candidate);
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error(`Local sync event path escapes sync root: ${relPath}`);
  }
  return candidate;
}

async function sha256File(absPath: string): Promise<string> {
  const hash = crypto.createHash('sha256');
  const stream = createReadStream(absPath);
  for await (const chunk of stream) {
    hash.update(chunk as Buffer);
  }
  return hash.digest('hex');
}

async function fileAsBlob(absPath: string, mimeType: string): Promise<Blob> {
  if (openAsBlob) {
    return openAsBlob(absPath, { type: mimeType });
  }

  const buffer = await fs.readFile(absPath);
  const bytes = new Uint8Array(buffer.buffer, buffer.byteOffset, buffer.byteLength);
  return new Blob([bytes], { type: mimeType });
}

interface MultipartArgs {
  batchId: string;
  timestamp?: number;
  events: WatchEvent[];
  resetBeforeApply?: boolean;
  rootPath: string;
}

async function buildMultipartUploadBody(
  { batchId, timestamp, events, resetBeforeApply, rootPath }: MultipartArgs,
): Promise<FormData> {
  const manifestEvents: Array<WatchEvent & { contentField?: string; sha256?: string; mimeType?: string }> = [];
  const form = new FormData();
  let fileIndex = 0;

  for (const event of events || []) {
    if (!eventNeedsContent(event)) {
      manifestEvents.push(event);
      continue;
    }

    const contentField = `file_${fileIndex}`;
    fileIndex += 1;
    const absPath = resolveEventFilePath(rootPath, event.path);
    const mimeType = mimeTypeForPath(event.path);
    const [sha256, content] = await Promise.all([
      sha256File(absPath),
      fileAsBlob(absPath, mimeType),
    ]);
    const filename = path.basename(event.path) || contentField;
    form.append(contentField, content, filename);
    manifestEvents.push({
      ...event,
      contentField,
      sha256,
      mimeType,
      size: event.size != null ? event.size : content.size,
    });
  }

  form.append('manifest', JSON.stringify({
    batchId,
    events: manifestEvents,
    timestamp: timestamp != null ? timestamp : Date.now(),
    ...(resetBeforeApply === true ? { resetBeforeApply: true } : {}),
  }));

  return form;
}

/**
 * Dispatch a file-event batch with automatic retry on transient errors
 * (network, 5xx, 429 with Retry-After) and a hook for token refresh on 401.
 */
export async function dispatchFileEventBatch({
  apiBaseUrl,
  accessToken,
  connectorId,
  batchId,
  timestamp,
  events,
  resetBeforeApply,
  rootPath,
  refreshAccessToken,
}: DispatchFileEventBatchArgs): Promise<unknown> {
  const baseUrl = trimTrailingSlash(apiBaseUrl);
  if (!baseUrl) throw new Error('Missing API base URL for local sync dispatch');
  if (!accessToken) throw new Error('Missing access token for local sync dispatch');
  if (!connectorId) throw new Error('Missing connector ID for local sync dispatch');

  const shouldUploadContent = !!rootPath;
  const url = `${baseUrl}/api/v1/connectors/${encodeURIComponent(connectorId)}/file-events${shouldUploadContent ? '/upload' : ''}`;
  const body: FormData | string = shouldUploadContent
    ? await buildMultipartUploadBody({
      batchId,
      timestamp,
      events,
      resetBeforeApply,
      rootPath: rootPath as string,
    })
    : JSON.stringify({
      batchId,
      events,
      timestamp: timestamp != null ? timestamp : Date.now(),
      ...(resetBeforeApply === true ? { resetBeforeApply: true } : {}),
    });

  let token = accessToken;
  let attempted401Refresh = false;
  let lastError: unknown;

  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt += 1) {
    let response: Response;
    try {
      response = await postWithTimeout(url, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          ...(shouldUploadContent ? {} : { 'Content-Type': 'application/json' }),
        },
        body,
      });
    } catch (err) {
      // Network error — retry with backoff.
      lastError = err;
      if (attempt === MAX_ATTEMPTS - 1) break;
      await sleep(BASE_BACKOFF_MS * 2 ** attempt);
      continue;
    }

    if (response.ok) {
      try { return await response.json(); } catch { return null; }
    }

    // 401 → try refresh once.
    if (
      response.status === HttpStatusCode.Unauthorized
      && !attempted401Refresh
      && typeof refreshAccessToken === 'function'
    ) {
      attempted401Refresh = true;
      try {
        const fresh = await refreshAccessToken();
        if (fresh) { token = fresh; continue; }
      } catch { /* fall through */ }
    }

    // 429 / 5xx → retry.
    if (response.status === HttpStatusCode.TooManyRequests || isServerErrorStatus(response.status)) {
      const retryAfter = computeRetryAfterMs(response);
      const wait = retryAfter != null ? retryAfter : BASE_BACKOFF_MS * 2 ** attempt;
      let parsed: unknown = null;
      try { parsed = await response.json(); } catch { /* ignore */ }
      lastError = new Error(`File-event dispatch ${response.status}: ${JSON.stringify(parsed)}`);
      if (attempt === MAX_ATTEMPTS - 1) break;
      await sleep(wait);
      continue;
    }

    // Non-retryable error.
    let parsed: unknown = null;
    try { parsed = await response.json(); } catch { /* ignore */ }
    throw new Error(`File-event dispatch failed (${response.status}): ${JSON.stringify(parsed)}`);
  }

  throw lastError instanceof Error ? lastError : new Error('File-event dispatch failed after retries');
}
