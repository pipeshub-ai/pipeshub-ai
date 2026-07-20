import type { ChatArtifact } from '../types';

/**
 * Parse `::download_conversation_task[label](url)` markers out of streamed
 * assistant content. The backend emits one marker per downloadable artifact
 * (e.g. "the full CSV of the query result"). We strip them from the rendered
 * markdown and surface them as Download buttons instead.
 */
export function parseDownloadMarkers(content: string): {
  text: string;
  tasks: Array<{ fileName: string; url: string }>;
} {
  const tasks: Array<{ fileName: string; url: string }> = [];
  const regex = /::download_conversation_task\[([^\]]+)\]\(([^)]+)\)/g;
  const text = content.replace(regex, (_, fileName, url) => {
    tasks.push({ fileName: fileName?.trim() || 'Download', url: (url ?? '').trim() });
    return '';
  });
  return { text: text.trimEnd(), tasks };
}

/** Fallback mime→artifact-type mapping for markers persisted before the
 * marker format carried an explicit artifactType segment. Mirrors the
 * backend's `MIME_TO_ARTIFACT_TYPE` (app/sandbox/artifact_upload.py). */
function artifactTypeFromMime(mime: string): string {
  if (mime.startsWith('image/')) return 'IMAGE';
  if (mime === 'text/csv' || mime.includes('spreadsheetml') || mime === 'application/vnd.ms-excel')
    return 'SPREADSHEET';
  if (mime.includes('presentationml') || mime === 'application/vnd.ms-powerpoint')
    return 'PRESENTATION';
  if (
    mime === 'application/pdf' ||
    mime.includes('wordprocessingml') ||
    mime === 'text/html' ||
    mime === 'text/markdown'
  )
    return 'DOCUMENT';
  if (mime === 'application/json') return 'DATA_FILE';
  return 'OTHER';
}

/**
 * Parse `::artifact[fileName](downloadUrl){mime|documentId|recordId|artifactType|version}`
 * markers from the assistant's final answer content (the last two segments are
 * optional — older persisted markers only carry the first three). These are
 * appended by the backend when sandbox tools (coding / database) generate
 * output files, and are the persistent record of artifacts once SSE streaming
 * ends.
 *
 * During streaming, artifacts are delivered via SSE `artifact` events; those
 * live in the slot's transient `artifacts` array. After completion, the saved
 * message content is the source of truth — parse the markers back into
 * `ChatArtifact` entries so the panel keeps rendering.
 *
 * Deduplicates by artifact identity: conversations persisted with repeated
 * markers for the same artifact version (a model re-running the same code)
 * render one card per artifact, not one per re-run.
 */
export function parseArtifactMarkers(content: string): {
  text: string;
  artifacts: ChatArtifact[];
} {
  const artifacts: ChatArtifact[] = [];
  const seen = new Set<string>();
  // Greedy on name/url, but braces are delimited; meta segments may be empty.
  const regex = /::artifact\[([^\]]+)\]\(([^)]+)\)\{([^}]*)\}/g;
  const text = content.replace(regex, (_, fileName, url, meta) => {
    const [mime = '', docId = '', recordId = '', rawType = '', rawVersion = ''] =
      String(meta).split('|');
    const cleanName = String(fileName).trim() || 'artifact';
    const cleanUrl = String(url).trim();
    const cleanMime = mime.trim() || 'application/octet-stream';
    const cleanRecordId = recordId.trim();
    const cleanDocId = docId.trim();
    const cleanType = rawType.trim();
    const parsedVersion = Number.parseInt(rawVersion.trim(), 10);
    const version = Number.isNaN(parsedVersion) ? undefined : parsedVersion;

    const dedupeKey = `${cleanRecordId || cleanDocId || cleanUrl}:${version ?? ''}`;
    if (seen.has(dedupeKey)) return '';
    seen.add(dedupeKey);

    artifacts.push({
      id: cleanRecordId || cleanDocId || `artifact-${artifacts.length}-${cleanName}`,
      fileName: cleanName,
      mimeType: cleanMime,
      sizeBytes: 0,
      downloadUrl: cleanUrl,
      artifactType: cleanType || artifactTypeFromMime(cleanMime),
      recordId: cleanRecordId || undefined,
      version,
    });
    return '';
  });
  return { text: text.trimEnd(), artifacts };
}

/**
 * Detect S3 / Azure Blob presigned URLs. Presigned URLs carry their own auth
 * in the query string, so we must NOT attach the user's bearer token — and
 * the download has to be a direct anchor click, not an axios blob fetch.
 */
export function isSignedUrl(url: string): boolean {
  return (
    url.includes('X-Amz-Signature') ||
    url.includes('AWSAccessKeyId') ||
    (url.includes('se=') && url.includes('sig='))
  );
}

/**
 * Marker URLs come from the assistant stream, which is a prompt-injection
 * surface in a RAG app. Only URLs pointing at OUR configured backend may be
 * fetched through the authenticated apiClient — anything else must go through
 * a direct anchor click so the user's bearer token is never attached to a
 * foreign host.
 *
 * Trusted origin = `NEXT_PUBLIC_API_BASE_URL` when set (split deployment /
 * dev where the Next.js server and backend live on different ports), else
 * `window.location.origin` (single-origin production build).
 */
export function isTrustedApiUrl(url: string): boolean {
  try {
    const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? '';
    const trustedOrigin = apiBase
      ? new URL(apiBase, window.location.origin).origin
      : window.location.origin;
    const resolved = new URL(url, trustedOrigin);
    return resolved.origin === trustedOrigin;
  } catch {
    return false;
  }
}
