/**
 * Chat composer attachment type allowlists and validators.
 * Kept separate from chat-input.tsx so unit tests can import without
 * pulling in the full React composer tree.
 */

export const SUPPORTED_FILE_TYPES = [
  'PDF',
  'PNG',
  'JPEG',
  'JPG',
  'TXT',
  'MD',
  'DOCX',
  'XLSX',
  'CSV',
] as const;

export const ACCEPTED_MIME_TYPES: Record<string, string> = {
  'application/pdf': 'PDF',
  'image/png': 'PNG',
  'image/jpeg': 'JPEG',
  'image/jpg': 'JPEG',
  'text/plain': 'TXT',
  'text/markdown': 'MD',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'DOCX',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'XLSX',
  'text/csv': 'CSV',
  'text/tab-separated-values': 'CSV',
};

/** Extension fallback when browsers leave `file.type` empty (common on Windows). */
export const ACCEPTED_EXTENSIONS = [
  'pdf',
  'png',
  'jpeg',
  'jpg',
  'txt',
  'md',
  'docx',
  'xlsx',
  'csv',
  'tsv',
] as const;

export const PASTE_MIME_TO_EXT: Record<string, string> = {
  'application/pdf': 'pdf',
  'image/png': 'png',
  'image/jpeg': 'jpg',
  'image/jpg': 'jpg',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
  'text/csv': 'csv',
  'text/tab-separated-values': 'tsv',
  'text/plain': 'txt',
  'text/markdown': 'md',
};

export function isFileTypeSupported(file: Pick<File, 'type' | 'name'>): boolean {
  if (Object.keys(ACCEPTED_MIME_TYPES).includes(file.type)) return true;
  const ext = file.name.split('.').pop()?.toLowerCase() ?? '';
  return (ACCEPTED_EXTENSIONS as readonly string[]).includes(ext);
}

export function pasteFallbackExtension(file: Pick<File, 'type' | 'name'>): string {
  return (
    PASTE_MIME_TO_EXT[file.type]
    ?? file.name.split('.').pop()?.toLowerCase()
    ?? 'bin'
  );
}
