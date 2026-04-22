'use client';

import type { FileType, FilePreviewSource, TabConfig, PaginationVisibility } from './types';

/**
 * Get file type category from file extension or mime type
 */
export function getFileType(fileName: string, _mimeType?: string): FileType {
  const ext = fileName.split('.').pop()?.toLowerCase() || '';
  
  // Images
  if (['jpg', 'jpeg', 'png', 'gif', 'svg', 'webp', 'bmp'].includes(ext)) {
    return 'image';
  }
  
  // PDFs
  if (ext === 'pdf') {
    return 'pdf';
  }
  
  // Documents
  if (['doc', 'docx', 'odt', 'rtf'].includes(ext)) {
    return 'document';
  }
  
  // Spreadsheets
  if (['xls', 'xlsx', 'csv', 'ods'].includes(ext)) {
    return 'spreadsheet';
  }
  
  // Presentations
  if (['ppt', 'pptx', 'odp'].includes(ext)) {
    return 'presentation';
  }
  
  // Code files
  if (['js', 'ts', 'jsx', 'tsx', 'py', 'java', 'cpp', 'c', 'go', 'rs', 'rb', 'php', 'html', 'css', 'json', 'xml', 'yaml', 'yml', 'md'].includes(ext)) {
    return 'code';
  }
  
  // Text files
  if (['txt', 'log'].includes(ext)) {
    return 'text';
  }
  
  return 'unknown';
}

/**
 * Get tab configuration based on source
 */
export function getTabsForSource(_source: FilePreviewSource): TabConfig[] {
  return [
    { id: 'preview', label: 'Preview', visible: true },
    { id: 'file-details', label: 'File Details', visible: true },
  ];
}

/**
 * Format file size in human readable format
 */
export function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 Bytes';
  
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`;
}

/**
 * Format date string
 */
export function formatDate(dateString: string): string {
  const date = new Date(dateString);
  return new Intl.DateTimeFormat('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(date);
}

/**
 * Determine renderer type based on MIME type and file extension
 */
export function getRendererType(mimeType: string, fileName: string): string {
  // Prefer MIME type over extension
  if (mimeType) {
    if (mimeType === 'application/pdf') return 'pdf';
    // Legacy Word binary (preview via PDF conversion or download fallback)
    if (mimeType === 'application/msword') return 'document';
    if (mimeType.startsWith('image/')) return 'image';
    if (mimeType.startsWith('video/')) return 'media';
    if (mimeType.startsWith('audio/')) return 'media';
    if (mimeType === 'text/markdown') return 'markdown';
    if (mimeType === 'text/html') return 'html'; // Rendered in sandboxed iframe, not as source code
    // Spreadsheets
    if (mimeType === 'text/csv') return 'spreadsheet';
    if (mimeType === 'application/vnd.ms-excel') return 'spreadsheet';
    if (mimeType === 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet') return 'spreadsheet';
    // Word OOXML (.docx, .docm, .dotx)
    if (isOoxmlWordMime(mimeType)) return 'docx';
    // Citations / records sometimes use a bare extension token instead of a MIME string
    if (isBareWordOoxmlToken(mimeType)) return 'docx';
    // PowerPoint -> PDF (backend converts)
    if (mimeType === 'application/vnd.ms-powerpoint') return 'pdf';
    if (mimeType === 'application/vnd.openxmlformats-officedocument.presentationml.presentation') return 'pdf';
    if (mimeType.startsWith('text/')) return 'text';
  }

  // Fallback to extension
  const ext = fileName.split('.').pop()?.toLowerCase() || '';

  // Images
  if (['.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp', '.bmp', '.ico'].includes(`.${ext}`)) {
    return 'image';
  }

  // PDF
  if (ext === 'pdf') return 'pdf';

  // Markdown
  if (['.md', '.markdown', '.mdx'].includes(`.${ext}`)) return 'markdown';

  // HTML — rendered in sandboxed iframe via HtmlRenderer (not shown as source code)
  if (['.html', '.htm'].includes(`.${ext}`)) return 'html';

  // Code/Text
  if (['.txt', '.log', '.js', '.jsx', '.ts', '.tsx', '.py', '.java', '.c', '.cpp', '.h', '.hpp',
       '.cs', '.php', '.rb', '.go', '.rs', '.swift', '.kt', '.sh', '.bash', '.sql', '.yml', '.yaml',
       '.json', '.xml', '.css', '.scss', '.sass', '.less'].includes(`.${ext}`)) {
    return 'text';
  }

  // Video
  if (['.mp4', '.webm', '.ogg', '.mov', '.avi', '.mkv'].includes(`.${ext}`)) {
    return 'media';
  }

  // Audio
  if (['.mp3', '.wav', '.ogg', '.m4a', '.flac', '.aac'].includes(`.${ext}`)) {
    return 'media';
  }

  // Spreadsheets
  if (['.xls', '.xlsx', '.csv'].includes(`.${ext}`)) return 'spreadsheet';

  // Word documents (OOXML → client preview; legacy .doc → download or PDF after server conversion)
  if (['docx', 'docm', 'dotx'].includes(ext)) return 'docx';
  if (ext === 'doc') return 'document'; // Legacy binary unless stream was converted to PDF (see resolvePreviewMimeAfterStream)

  // PowerPoint -> PDF (backend converts)
  if (['.ppt', '.pptx'].includes(`.${ext}`)) return 'pdf';

  return 'unknown';
}


/** MIME types for PowerPoint presentation files (.ppt, .pptx) */
export const PPT_MIME_TYPES = [
  'application/vnd.ms-powerpoint',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation',
];

/** OOXML Word MIME type (.docx). */
export const DOCX_MIME_TYPE =
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document';

/** Other Word OOXML MIME types handled like .docx in the browser preview. */
const OOXML_WORD_MIME_TYPES: readonly string[] = [
  DOCX_MIME_TYPE,
  'application/vnd.ms-word.document.macroEnabled.12', // .docm
  'application/vnd.openxmlformats-officedocument.wordprocessingml.template', // .dotx
];

function isOoxmlWordMime(mimeType: string): boolean {
  return OOXML_WORD_MIME_TYPES.includes(mimeType);
}

/** Strips a leading dot and lowercases (handles API values like ".docx" or "DOCX"). */
export function normalizeFileExtension(ext?: string | null): string {
  if (!ext) return '';
  return ext.replace(/^\./, '').trim().toLowerCase();
}

/**
 * Some APIs put a bare extension where a MIME string is expected (e.g. `"docx"`).
 * Treat those like OOXML Word for routing and blob hand-off.
 */
function isBareWordOoxmlToken(value: string): boolean {
  return ['docx', 'docm', 'dotx'].includes(normalizeFileExtension(value));
}

/**
 * Legacy binary Word (.doc). OOXML Word uses {@link DOCX_MIME_TYPE} or .docx/.docm/.dotx extensions.
 * Used to decide when to request server-side PDF conversion for preview (connectors that support it).
 */
export function isLegacyWordDocFile(mimeType?: string, fileName?: string): boolean {
  if (mimeType === 'application/msword') return true;
  if (!fileName) return false;
  const ext = fileName.split('.').pop()?.toLowerCase() || '';
  return ext === 'doc';
}

/**
 * After `streamRecord`, align the MIME we pass to `getRendererType` with the actual response.
 * Fixes legacy `.doc` (and similar) previews when the backend returns a converted PDF blob while
 * record metadata still says `application/msword`.
 */
export function resolvePreviewMimeAfterStream(
  recordMime: string,
  fileName: string,
  blob: Blob,
  requestedPdfConversion: boolean,
): string {
  const base = (recordMime || '').trim();
  if (requestedPdfConversion && blob.type === 'application/pdf') {
    return 'application/pdf';
  }
  // Stream response Content-Type is authoritative when it names Word OOXML
  if (blob.type && isOoxmlWordMime(blob.type)) {
    return blob.type;
  }
  if (!base && blob.type) return blob.type;
  return base || blob.type || '';
}

/**
 * Checks whether a file is a PowerPoint presentation (PPT/PPTX) by MIME type or extension.
 * PPT/PPTX files require server-side conversion to PDF via the `convertTo=pdf` query param
 * on the streaming API before they can be previewed in the browser.
 */
export function isPresentationFile(mimeType?: string, fileName?: string): boolean {
  if (mimeType && PPT_MIME_TYPES.includes(mimeType)) return true;
  if (fileName) {
    const ext = fileName.split('.').pop()?.toLowerCase();
    if (ext === 'ppt' || ext === 'pptx') return true;
  }
  return false;
}

/**
 * Checks whether a file is an OOXML Word doc (.docx / .docm / .dotx). DOCX is rendered client-side
 * by `docx-preview` directly from the in-memory Blob, so we skip `createObjectURL`
 * and the extra `fetch` round-trip the DOCX renderer would otherwise perform.
 *
 * @param extraExtensionHints — e.g. `fileRecord.extension` from record details (may include a leading dot).
 */
export function isDocxFile(
  mimeType?: string,
  fileName?: string,
  ...extraExtensionHints: Array<string | undefined | null>
): boolean {
  const mime = (mimeType || '').trim();
  if (mime && isOoxmlWordMime(mime)) return true;
  if (mime && isBareWordOoxmlToken(mime)) return true;

  const names = [fileName, ...extraExtensionHints];
  for (const raw of names) {
    if (!raw) continue;
    const extFromPath = raw.includes('.') ? (raw.split('.').pop() || '') : raw;
    if (isBareWordOoxmlToken(extFromPath)) return true;
    if (isBareWordOoxmlToken(normalizeFileExtension(raw))) return true;
  }
  return false;
}

/**
 * Determine if pagination controls should be visible
 */
export function shouldShowPagination(
  fileType: string,
  fileName: string,
  totalPages: number | null,
  isLoading: boolean,
  hasError: boolean
): PaginationVisibility {
  // Don't show during loading or error states
  if (isLoading) return { shouldShow: false, reason: 'loading' };
  if (hasError) return { shouldShow: false, reason: 'error' };

  // Only PDFs support pagination currently
  const rendererType = getRendererType(fileType, fileName);
  if (rendererType !== 'pdf') {
    return { shouldShow: false, reason: 'unsupported' };
  }

  // Show pagination for all PDFs (including single-page, to show "1 / 1")
  return { shouldShow: true };
}
