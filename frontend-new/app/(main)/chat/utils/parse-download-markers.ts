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
