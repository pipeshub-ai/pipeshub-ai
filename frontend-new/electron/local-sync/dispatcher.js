const MAX_ATTEMPTS = 5;
const BASE_BACKOFF_MS = 500;
const REQUEST_TIMEOUT_MS = 30000;

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
  refreshAccessToken,
}) {
  const baseUrl = trimTrailingSlash(apiBaseUrl);
  if (!baseUrl) throw new Error('Missing API base URL for local sync dispatch');
  if (!accessToken) throw new Error('Missing access token for local sync dispatch');
  if (!connectorId) throw new Error('Missing connector ID for local sync dispatch');

  const url = `${baseUrl}/api/v1/connectors/${encodeURIComponent(connectorId)}/file-events`;
  const body = JSON.stringify({
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
          'Content-Type': 'application/json',
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
