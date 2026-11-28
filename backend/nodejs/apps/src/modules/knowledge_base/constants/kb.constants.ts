// Knowledge Base Upload Limits - central constants

export const KB_UPLOAD_LIMITS = {
  // Maximum number of files accepted per upload request
  maxFilesPerRequest: 1000,
  // Default per-file size limit (bytes) if platform settings cannot be resolved
  defaultMaxFileSizeBytes: 30 * 1024 * 1024, // 30 MB
} as const;

/**
 * Programs and libraries. The storage MIME table knows them, but a knowledge base cannot read
 * them, and accepting them only lets a person store an executable under a "Completed" upload.
 */
export const KB_BLOCKED_UPLOAD_EXTENSIONS: ReadonlySet<string> = new Set([
  'exe',
  'dll',
  'so',
  'dylib',
]);

/** The extensions (lower-case, no dot) and MIME types a knowledge base upload accepts. */
export function kbUploadAllowlist(
  extensionToMimeType: Readonly<Record<string, string>>,
): {
  allowedExtensions: string[];
  allowedMimeTypes: string[];
} {
  const allowed = Object.entries(extensionToMimeType)
    .map(([ext, mime]) => [ext.toLowerCase(), mime] as const)
    .filter(([ext]) => !KB_BLOCKED_UPLOAD_EXTENSIONS.has(ext));
  return {
    allowedExtensions: allowed.map(([ext]) => ext),
    allowedMimeTypes: [...new Set(allowed.map(([, mime]) => mime))],
  };
}
