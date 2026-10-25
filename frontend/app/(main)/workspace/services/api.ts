import { apiClient } from '@/lib/api';

const VECTOR_DB_MEMORY_URL = '/api/v1/health/vector-db/memory';

/** Response of GET /api/v1/health/vector-db/memory. */
export interface VectorDbMemoryReport {
  status: 'ok' | 'insufficient' | 'unknown' | 'unsupported';
  neededBytes?: number;
  availableBytes?: number | null;
  availableSource?: 'configured' | 'host' | null;
}

/**
 * Advisory: null when the report cannot be fetched, so the page simply shows
 * no warning rather than an error.
 */
export async function getVectorDbMemoryReport(): Promise<VectorDbMemoryReport | null> {
  try {
    const { data } = await apiClient.get<VectorDbMemoryReport>(VECTOR_DB_MEMORY_URL, {
      suppressErrorToast: true,
    });
    return data;
  } catch {
    return null;
  }
}
