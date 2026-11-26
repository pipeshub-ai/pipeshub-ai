import { FILE_ICON_MAP } from '@/lib/constants/file-icons';
import type { FileIconStyle, FileIconExtension } from '@/lib/constants/file-icons';

// For now, always use light mode (TODO: use theme when dark icons available)
const USE_LIGHT_MODE_ALWAYS = true;

/**
 * Get the full path to a file icon
 * @param extension - File extension (e.g., 'pdf', 'jpg')
 * @param style - Icon style: 'color', 'flat', or 'line' (default: 'color')
 * @param theme - Theme mode: 'light' or 'dark' (default: 'light')
 * @returns Icon path or null if not found
 */
export function getFileIconPath(
  extension: string,
  style: FileIconStyle = 'color',
  theme: 'light' | 'dark' = 'light'
): string | null {
  const normalizedExt = normalizeExtension(extension);

  if (!FILE_ICON_MAP[normalizedExt as FileIconExtension]) {
    return null; // Icon not found
  }

  // For now, always use light mode icons (dark folder is empty)
  const folder = USE_LIGHT_MODE_ALWAYS
    ? 'light-file-icons'
    : theme === 'dark'
    ? 'dark-file-icons'
    : 'light-file-icons';

  return `/icons/${folder}/${normalizedExt}-text-${style}.svg`;
}

/**
 * Extract file extension from filename
 * @param filename - Full filename with extension
 * @returns Extension without dot, or null if no extension
 */
export function getFileExtension(filename: string): string | null {
  const match = filename.match(/\.([^.]+)$/);
  return match ? match[1].toLowerCase() : null;
}

/**
 * Normalize file extension (lowercase, remove dot, handle special cases)
 * @param ext - File extension
 * @returns Normalized extension
 */
export function normalizeExtension(ext: string): string {
  const normalized = ext.toLowerCase().replace(/^\./, '');

  // Handle special cases - map to available icons
  if (normalized === 'jpeg') return 'jpg';
  if (normalized === 'docx') return 'doc'; // Use same icon for doc/docx

  return normalized;
}

/**
 * Check if a file icon is available for the given extension
 * @param extension - File extension to check
 * @returns True if icon exists, false otherwise
 */
export function isFileIconAvailable(extension: string): boolean {
  const normalized = normalizeExtension(extension);
  return normalized in FILE_ICON_MAP;
}

const OCTET_STREAM = 'application/octet-stream';

/** Types by extension, mirroring the backend's `harness_core/mime.py`: a file stored before its type was
 * detected (octet-stream) still opens, labels and badges as what it is. */
const EXTENSION_TO_MIME: Record<string, string> = {
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  gif: 'image/gif',
  svg: 'image/svg+xml',
  webp: 'image/webp',
  pdf: 'application/pdf',
  csv: 'text/csv',
  json: 'application/json',
  html: 'text/html',
  txt: 'text/plain',
  md: 'text/markdown',
  doc: 'application/msword',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  xls: 'application/vnd.ms-excel',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  ppt: 'application/vnd.ms-powerpoint',
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  xml: 'application/xml',
  zip: 'application/zip',
};

/** `mimeType` when it names a type; otherwise the type of `fileName`'s extension, else octet-stream. */
export function effectiveMimeType(fileName: string, mimeType?: string | null): string {
  const given = mimeType?.trim();
  if (given && given !== OCTET_STREAM) return given;
  const ext = getFileExtension(fileName || '');
  return (ext && EXTENSION_TO_MIME[ext]) || OCTET_STREAM;
}

/**
 * Extract file extension from MIME type
 * @param mimeType - MIME type (e.g., 'application/pdf', 'image/jpeg')
 * @returns Extension without dot, or null if not recognized
 */
export function getMimeTypeExtension(mimeType: string): string | null {
  // Map common MIME types to extensions
  const mimeMap: Record<string, string> = {
    'application/pdf': 'pdf',
    'application/msword': 'doc',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'doc',
    'application/vnd.ms-excel': 'xls',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xls',
    'application/vnd.ms-powerpoint': 'ppt',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'ppt',
    'text/plain': 'txt',
    'text/markdown': 'md',
    'text/x-markdown': 'md',
    'text/html': 'html',
    'text/css': 'css',
    'application/javascript': 'js',
    'text/javascript': 'js',
    'application/json': 'json',
    'image/png': 'png',
    'image/jpeg': 'jpg',
    'image/jpg': 'jpg',
    'image/gif': 'gif',
    'image/webp': 'webp',
    'image/svg+xml': 'svg',
    'image/x-icon': 'ico',
    'image/tiff': 'tiff',
    'video/mp4': 'mp4',
    'video/quicktime': 'mov',
    'video/mpeg': 'mpg',
    'video/x-msvideo': 'avi',
    'audio/mpeg': 'mp3',
    'audio/wav': 'wav',
    'application/zip': 'zip',
    'application/x-rar-compressed': 'rar',
  };

  return mimeMap[mimeType] || null;
}
