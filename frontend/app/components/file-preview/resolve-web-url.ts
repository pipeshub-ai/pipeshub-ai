'use client';

/**
 * Shared web-URL resolution for record / file preview surfaces.
 *
 * Mirrors the previous `resolveWebUrl` in `record-view-shell.tsx`:
 * - respects `hideWeburl`
 * - tries each candidate independently (invalid primary must not block fallback)
 * - normalizes relative UPLOAD paths to an absolute same-origin URL
 */

export type WebUrlRecordFields = {
  hideWeburl?: boolean;
  origin?: string;
  webUrl?: string | null;
  fileRecord?: { webUrl?: string | null } | null;
};

function normalizeWebUrlCandidate(
  raw: string,
  origin?: string,
): string | null {
  const url = raw.trim();
  if (!url) return null;

  if (/^https?:\/\//i.test(url)) return url;

  // Relative / non-http upload paths — same-origin absolute URL (record-view-shell).
  if (origin === 'UPLOAD') {
    const path = url.startsWith('/') ? url : `/${url}`;
    if (typeof window === 'undefined') return path;
    return `${window.location.protocol}//${window.location.host}${path}`;
  }

  return null;
}

/**
 * Resolve the best external/source URL for a record (and optional file override).
 *
 * Candidate order: `fileWebUrl` → `record.webUrl` → `record.fileRecord.webUrl`.
 */
export function resolveWebUrl(
  record?: WebUrlRecordFields | null,
  fileWebUrl?: string | null,
): string | null {
  if (record?.hideWeburl) return null;

  const candidates = [fileWebUrl, record?.webUrl, record?.fileRecord?.webUrl];
  for (const candidate of candidates) {
    if (candidate == null) continue;
    const resolved = normalizeWebUrlCandidate(String(candidate), record?.origin);
    if (resolved) return resolved;
  }
  return null;
}
