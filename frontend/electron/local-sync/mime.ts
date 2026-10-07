import * as path from 'path';

const MIME_BY_EXT = new Map<string, string>(Object.entries({
  txt: 'text/plain',
  log: 'text/plain',
  md: 'text/markdown',
  mdx: 'text/mdx',
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

/** Content-Type for a byte stream, where an unrecognised kind is still bytes. */
export function mimeTypeForPath(relPath: string): string {
  return mimeTypeForPathOrUndefined(relPath) || 'application/octet-stream';
}

/**
 * MIME for a sync event, or undefined when the extension is unknown.
 *
 * The connector resolves `event.mimeType or guess_type(name) or UNKNOWN`, so
 * any non-empty string sent here wins outright. Falling back to
 * `application/octet-stream` would lock out the server's own guess and store a
 * type that maps back to no extension at all.
 */
export function mimeTypeForPathOrUndefined(relPath: string): string | undefined {
  const ext = path.extname(String(relPath || '')).replace(/^\./, '').toLowerCase();
  return MIME_BY_EXT.get(ext);
}
