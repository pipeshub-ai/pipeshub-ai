import { useEffect, useState } from 'react';
import { KnowledgeBaseApi } from '@/app/(main)/knowledge-base/api';

const DEFAULT_MAX_FILE_SIZE_MB = 30;
export const DEFAULT_MAX_FILE_SIZE_BYTES = DEFAULT_MAX_FILE_SIZE_MB * 1024 * 1024;

interface UploadLimitsResponse {
  maxFileSizeBytes?: number;
}

// Last known value, used to seed state on mount so there's no flash back to the
// default while a fresh value is fetched.
let cachedBytes: number | null = null;
// Dedupe concurrent fetches (e.g. the page and the upload dialog mounting
// together) WITHOUT caching the result across mounts — the limit is an
// admin-editable platform setting, so it must be re-read each time the hook
// mounts (e.g. after the user changes it in Labs and navigates here).
let inFlight: Promise<number | null> | null = null;

function fetchUploadLimit(): Promise<number | null> {
  if (inFlight) return inFlight;
  inFlight = KnowledgeBaseApi.getUploadLimits()
    .then((resp) => {
      const bytes = Number((resp as UploadLimitsResponse)?.maxFileSizeBytes);
      return Number.isFinite(bytes) && bytes > 0 ? bytes : null;
    })
    .catch(() => null)
    .finally(() => {
      // Clear so the NEXT mount triggers a fresh read instead of reusing a
      // stale promise (the cause of the limit not updating after a Labs change).
      inFlight = null;
    });
  return inFlight;
}

/**
 * Fetches KB upload limits from the backend on every mount (deduping concurrent
 * requests) so a limit changed in Labs is reflected as soon as the user
 * navigates to a page that reads it. Returns the raw byte limit and a rounded
 * MB value for display.
 */
export function useUploadLimits() {
  const [maxFileSizeBytes, setMaxFileSizeBytes] = useState(
    cachedBytes ?? DEFAULT_MAX_FILE_SIZE_BYTES,
  );

  useEffect(() => {
    let mounted = true;
    fetchUploadLimit().then((bytes) => {
      if (bytes == null) return;
      cachedBytes = bytes;
      if (mounted) setMaxFileSizeBytes(bytes);
    });
    return () => {
      mounted = false;
    };
  }, []);

  return {
    maxFileSizeBytes,
    maxFileSizeMB: Math.round(maxFileSizeBytes / (1024 * 1024)),
  };
}
