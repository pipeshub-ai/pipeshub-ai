import { useEffect, useState } from 'react';
import { KnowledgeBaseApi } from '@/app/(main)/knowledge-base/api';

const DEFAULT_MAX_FILE_SIZE_MB = 30;
export const DEFAULT_MAX_FILE_SIZE_BYTES = DEFAULT_MAX_FILE_SIZE_MB * 1024 * 1024;

interface UploadLimitsResponse {
  maxFileSizeBytes?: number;
}

// Module-level cache to avoid duplicate fetches when multiple components mount the hook.
let cachedPromise: Promise<UploadLimitsResponse | null> | null = null;

/**
 * Fetches KB upload limits from the backend once and memoizes the result.
 * Returns both the raw byte limit and a rounded MB value for display.
 */
export function useUploadLimits() {
  const [maxFileSizeBytes, setMaxFileSizeBytes] = useState(DEFAULT_MAX_FILE_SIZE_BYTES);

  useEffect(() => {
    let mounted = true;
    if (!cachedPromise) {
      cachedPromise = KnowledgeBaseApi.getUploadLimits()
        .then((resp) => resp as UploadLimitsResponse)
        .catch(() => null);
    }
    cachedPromise.then((resp) => {
      if (!mounted) return;
      const s = Number(resp?.maxFileSizeBytes);
      if (Number.isFinite(s) && s > 0) setMaxFileSizeBytes(s);
    });
    return () => { mounted = false; };
  }, []);

  return {
    maxFileSizeBytes,
    maxFileSizeMB: Math.round(maxFileSizeBytes / (1024 * 1024)),
  };
}
