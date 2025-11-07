// Knowledge Base Upload Limits - central constants

export const KB_UPLOAD_LIMITS = {
  // Maximum number of files accepted per upload request
  maxFilesPerRequest: 1000,
  // Default per-file size limit (bytes) if platform settings cannot be resolved
  defaultMaxFileSizeBytes: 30 * 1024 * 1024, // 30 MB
} as const;


