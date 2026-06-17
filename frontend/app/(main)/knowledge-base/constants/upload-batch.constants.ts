/**
 * Upload batching configuration.
 *
 * All limits are adjustable — change these values based on Cloudflare plan
 * limits, observed upload performance, and backend capacity. No algorithm
 * code needs to change when tuning these.
 */
export const UPLOAD_BATCH_CONFIG = {
  /**
   * Maximum cumulative file size (bytes) for a single FormData request.
   * Files whose individual size exceeds this are sent as single-file requests.
   * Set below Cloudflare's request body limit (100MB free/pro, 500MB enterprise)
   * to leave room for multipart envelope overhead.
   *
   * Default: 50 MB
   */
  maxBatchBytes: 50 * 1024 * 1024,

  /**
   * Maximum number of files in a single batch, regardless of total size.
   * Prevents backend memory pressure from hundreds of tiny files in one
   * multipart request (multer buffers all files before processing starts).
   *
   * Default: 10
   */
  maxFilesPerBatch: 10,

  /**
   * Number of batch upload requests sent in parallel. Each batch is an
   * independent SSE stream. Higher values speed up large uploads but
   * increase backend load and risk rate-limiting.
   *
   * Default: 5
   */
  uploadConcurrency: 5,
} as const;
