const crypto = require('crypto');
const fs = require('fs/promises');
const path = require('path');

const MAX_ATTEMPTS = 5;
const BASE_BACKOFF_MS = 500;
const REQUEST_TIMEOUT_MS = 30000;
const CONTENT_EVENT_TYPES = new Set(['CREATED', 'MODIFIED', 'RENAMED', 'MOVED']);
const MIME_BY_EXT = new Map(Object.entries({
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

function trimTrailingSlash(value) {
  return String(value || '').replace(/\/$/, '');
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function computeRetryAfterMs(response) {
  const header = response.headers && response.headers.get && response.headers.get('retry-after');
  if (!header) return null;
  const asSeconds = Number(header);
  if (Number.isFinite(asSeconds) && asSeconds >= 0) return Math.min(asSeconds * 1000, 60000);
  const when = Date.parse(header);
  if (!Number.isNaN(when)) return Math.max(0, Math.min(when - Date.now(), 60000));
  return null;
}

async function postWithTimeout(url, init) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

function eventNeedsContent(event) {
  return !!event
    && !event.isDirectory
    && CONTENT_EVENT_TYPES.has(String(event.type || '').toUpperCase());
}

function mimeTypeForPath(relPath) {
  const ext = path.extname(String(relPath || '')).replace(/^\./, '').toLowerCase();
  return MIME_BY_EXT.get(ext) || 'application/octet-stream';
}

function resolveEventFilePath(rootPath, relPath) {
  const root = path.resolve(String(rootPath || ''));
  const candidate = path.resolve(root, String(relPath || ''));
  const relative = path.relative(root, candidate);
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error(`Local sync event path escapes sync root: ${relPath}`);
  }
  return candidate;
}

async function buildMultipartUploadBody({ batchId, timestamp, events, resetBeforeApply, rootPath }) {
  const manifestEvents = [];
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
    const content = await fs.readFile(absPath);
    const sha256 = crypto.createHash('sha256').update(content).digest('hex');
    const mimeType = mimeTypeForPath(event.path);
    const filename = path.basename(event.path) || contentField;
    form.append(
      contentField,
      new Blob([content], { type: mimeType }),
      filename,
    );
    manifestEvents.push({
      ...event,
      contentField,
      sha256,
      mimeType,
      size: event.size != null ? event.size : content.length,
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
 *
 * @param {object} args
 * @param {() => Promise<string|null>} [args.refreshAccessToken] Called once on 401 to fetch a fresh token.
 */
async function dispatchFileEventBatch({
  apiBaseUrl,
  accessToken,
  connectorId,
  batchId,
  timestamp,
  events,
  resetBeforeApply,
  rootPath,
  refreshAccessToken,
}) {
  const baseUrl = trimTrailingSlash(apiBaseUrl);
  if (!baseUrl) throw new Error('Missing API base URL for local sync dispatch');
  if (!accessToken) throw new Error('Missing access token for local sync dispatch');
  if (!connectorId) throw new Error('Missing connector ID for local sync dispatch');

  const shouldUploadContent = !!rootPath;
  const url = `${baseUrl}/api/v1/connectors/${encodeURIComponent(connectorId)}/file-events${shouldUploadContent ? '/upload' : ''}`;
  const body = shouldUploadContent
    ? await buildMultipartUploadBody({
      batchId,
      timestamp,
      events,
      resetBeforeApply,
      rootPath,
    })
    : JSON.stringify({
      batchId,
      events,
      timestamp: timestamp != null ? timestamp : Date.now(),
      ...(resetBeforeApply === true ? { resetBeforeApply: true } : {}),
    });

  let token = accessToken;
  let attempted401Refresh = false;
  let lastError;

  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt += 1) {
    let response;
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
    if (response.status === 401 && !attempted401Refresh && typeof refreshAccessToken === 'function') {
      attempted401Refresh = true;
      try {
        const fresh = await refreshAccessToken();
        if (fresh) { token = fresh; continue; }
      } catch { /* fall through */ }
    }

    // 429 / 5xx → retry.
    if (response.status === 429 || (response.status >= 500 && response.status < 600)) {
      const retryAfter = computeRetryAfterMs(response);
      const wait = retryAfter != null ? retryAfter : BASE_BACKOFF_MS * 2 ** attempt;
      let parsed = null;
      try { parsed = await response.json(); } catch { /* ignore */ }
      lastError = new Error(`File-event dispatch ${response.status}: ${JSON.stringify(parsed)}`);
      if (attempt === MAX_ATTEMPTS - 1) break;
      await sleep(wait);
      continue;
    }

    // Non-retryable error.
    let parsed = null;
    try { parsed = await response.json(); } catch { /* ignore */ }
    throw new Error(`File-event dispatch failed (${response.status}): ${JSON.stringify(parsed)}`);
  }

  throw lastError || new Error('File-event dispatch failed after retries');
}

module.exports = { dispatchFileEventBatch };
