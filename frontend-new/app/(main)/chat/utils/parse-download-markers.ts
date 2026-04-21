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
