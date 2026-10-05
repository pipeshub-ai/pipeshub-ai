import { apiClient } from '@/lib/api';
import type { BulkRestoreResponse, RestoreResponse, TrashListResponse } from './types';

const BASE_URL = '/api/v1/knowledgeBase';

/** The page shows its own messages, so these calls never raise the global error toast. */
export const TrashApi = {
  async list(kbId: string, page: number, limit: number): Promise<TrashListResponse> {
    const { data } = await apiClient.get<TrashListResponse>(
      `${BASE_URL}/${encodeURIComponent(kbId)}/trash`,
      { params: { page, limit }, suppressErrorToast: true },
    );
    return data;
  },

  async collectionName(kbId: string): Promise<string> {
    const { data } = await apiClient.get<{ name?: unknown }>(
      `${BASE_URL}/${encodeURIComponent(kbId)}`,
      { suppressErrorToast: true },
    );
    return typeof data?.name === 'string' ? data.name : '';
  },

  async restoreOne(recordId: string): Promise<RestoreResponse> {
    const { data } = await apiClient.post<RestoreResponse>(
      `${BASE_URL}/record/${encodeURIComponent(recordId)}/restore`,
      undefined,
      { suppressErrorToast: true },
    );
    return data;
  },

  async restoreMany(recordIds: string[]): Promise<BulkRestoreResponse> {
    const { data } = await apiClient.post<BulkRestoreResponse>(
      `${BASE_URL}/records/restore`,
      { recordIds },
      { suppressErrorToast: true },
    );
    return data;
  },
};
