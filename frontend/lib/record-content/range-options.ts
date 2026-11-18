/** What loading a large PDF by byte ranges is configured with. No imports, so a renderer can use it freely. */
const MIB = 1024 * 1024;

/** A PDF larger than this is opened by ranges. */
export const RECORD_RANGE_THRESHOLD_BYTES = 32 * MIB;
/** pdf.js's range chunk size, and the size of the first request. */
export const RECORD_RANGE_CHUNK_BYTES = 1 * MIB;
/** The server answers at most this span as a partial response, so a larger pdf.js request is split. */
export const RECORD_RANGE_MAX_SPAN_BYTES = 8 * MIB;

/** The options the PDF loader forwards to pdf.js's `getDocument`: no background fetch, no streaming, 1 MiB chunks. */
export const RECORD_RANGE_LOADER_OPTIONS = {
  disableAutoFetch: true,
  disableStream: true,
  rangeChunkSize: RECORD_RANGE_CHUNK_BYTES,
} as const;
