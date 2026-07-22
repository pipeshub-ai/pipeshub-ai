import type { ChatArtifact } from '../types';
import { buildChatArtifact } from './build-chat-artifact';

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

/** `(url)` placeholder the backend emits instead of a real (short-lived,
 * signed) URL once a marker carries a `recordId` — see `_append_task_markers`
 * (`app/utils/streaming.py`). Exists only to satisfy `parseArtifactMarkers`'s
 * regex, which requires a non-empty `(...)` segment; never a fetchable URL.
 * Older persisted markers embed a real signed/relative URL instead — those
 * keep parsing as before (`isSignedUrl`/`isTrustedApiUrl` still classify them
 * correctly), so this is purely additive back-compat. */
const RECORD_PLACEHOLDER_PREFIX = 'record:';

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
  let text = content.replace(regex, (_, fileName, url, meta) => {
    const [mime = '', docId = '', recordId = '', rawType = '', rawVersion = ''] =
      String(meta).split('|');
    const cleanName = String(fileName).trim() || 'artifact';
    const rawUrl = String(url).trim();
    // A `record:` placeholder is not a real URL at all — normalize it to ''
    // ("no direct URL") so every downstream consumer keeps using its
    // existing "falsy downloadUrl" handling instead of needing to special-
    // case this prefix itself.
    const cleanUrl = rawUrl.startsWith(RECORD_PLACEHOLDER_PREFIX) ? '' : rawUrl;
    const cleanMime = mime.trim() || 'application/octet-stream';
    const cleanRecordId = recordId.trim();
    const cleanDocId = docId.trim();
    const cleanType = rawType.trim();
    const parsedVersion = Number.parseInt(rawVersion.trim(), 10);
    const version = Number.isNaN(parsedVersion) ? undefined : parsedVersion;

    const dedupeKey = `${cleanRecordId || cleanDocId || cleanUrl}:${version ?? ''}`;
    if (seen.has(dedupeKey)) return '';
    seen.add(dedupeKey);

    artifacts.push(
      buildChatArtifact({
        id: cleanRecordId || cleanDocId || `artifact-${artifacts.length}-${cleanName}`,
        fileName: cleanName,
        mimeType: cleanMime,
        downloadUrl: cleanUrl,
        artifactType: cleanType || undefined,
        recordId: cleanRecordId || undefined,
        version,
      }),
    );
    return '';
  });
  // Strip short-form `::artifact[name]` or `::artifact[name](url)` remnants.
  // LLMs sometimes hallucinate the marker syntax without the full `{meta}`
  // block — these are not backend-authored and carry no useful metadata, so
  // just remove them to avoid rendering raw directive text in the markdown.
  text = text.replace(/::artifact\[[^\]]+\](?:\([^)]*\))?/g, '');
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
