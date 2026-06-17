import { UPLOAD_BATCH_CONFIG } from '../constants/upload-batch.constants';

interface SizedEntry {
  file: File;
}

interface BatchOptions {
  maxBatchBytes?: number;
  maxFilesPerBatch?: number;
}

/**
 * Partition file entries into batches whose cumulative size stays under a
 * configurable byte budget and file-count cap. Entries are sorted by size
 * ascending so small files pack efficiently into early batches. Files that
 * individually exceed `maxBatchBytes` are placed in their own single-file
 * batch so they never block smaller files from grouping together.
 */
export function createSizeBatches<T extends SizedEntry>(
  entries: T[],
  options?: BatchOptions,
): T[][] {
  const maxBytes = options?.maxBatchBytes ?? UPLOAD_BATCH_CONFIG.maxBatchBytes;
  const maxFiles =
    options?.maxFilesPerBatch ?? UPLOAD_BATCH_CONFIG.maxFilesPerBatch;

  if (entries.length === 0) return [];

  const sorted = [...entries].sort((a, b) => a.file.size - b.file.size);

  const batches: T[][] = [];
  let currentBatch: T[] = [];
  let currentSize = 0;

  for (const entry of sorted) {
    if (entry.file.size >= maxBytes) {
      if (currentBatch.length > 0) {
        batches.push(currentBatch);
        currentBatch = [];
        currentSize = 0;
      }
      batches.push([entry]);
      continue;
    }

    const wouldExceedSize = currentSize + entry.file.size > maxBytes;
    const wouldExceedCount = currentBatch.length >= maxFiles;

    if ((wouldExceedSize || wouldExceedCount) && currentBatch.length > 0) {
      batches.push(currentBatch);
      currentBatch = [];
      currentSize = 0;
    }

    currentBatch.push(entry);
    currentSize += entry.file.size;
  }

  if (currentBatch.length > 0) {
    batches.push(currentBatch);
  }

  return batches;
}
