import { apiClient } from '@/lib/api';
import type {
  ArtifactDetail,
  ArtifactGalleryVersion,
  ArtifactListParams,
  ArtifactListResponse,
  ArtifactMember,
} from './types';

const BASE_URL = '/api/v1/artifacts';

function toQuery(params: ArtifactListParams): Record<string, string | number> {
  const query: Record<string, string | number> = {
    page: params.page ?? 1,
    limit: params.limit ?? 50,
  };
  if (params.search) query.search = params.search;
  if (params.artifactTypes?.length) query.artifactTypes = params.artifactTypes.join(',');
  if (params.conversationId) query.conversationId = params.conversationId;
  if (params.dateFrom != null) query.dateFrom = params.dateFrom;
  if (params.dateTo != null) query.dateTo = params.dateTo;
  if (params.sortBy) query.sortBy = params.sortBy;
  if (params.sortOrder) query.sortOrder = params.sortOrder;
  if (params.ownership && params.ownership !== 'all') query.ownership = params.ownership;
  return query;
}

export const ArtifactsApi = {
  async list(params: ArtifactListParams = {}): Promise<ArtifactListResponse> {
    const { data } = await apiClient.get<ArtifactListResponse>(BASE_URL, {
      params: toQuery(params),
    });
    return data;
  },

  /** The caller reports a failure (a stale link says the artifact is gone), so no global toast. */
  async get(artifactId: string): Promise<ArtifactDetail> {
    const { data } = await apiClient.get<ArtifactDetail>(`${BASE_URL}/${artifactId}`, {
      suppressErrorToast: true,
    });
    return data;
  },

  async listVersions(artifactId: string): Promise<ArtifactGalleryVersion[]> {
    const { data } = await apiClient.get<{ versions: ArtifactGalleryVersion[] }>(
      `${BASE_URL}/${artifactId}/versions`,
    );
    return data.versions ?? [];
  },

  async listMembers(artifactId: string): Promise<ArtifactMember[]> {
    const { data } = await apiClient.get<{ members: ArtifactMember[] }>(
      `${BASE_URL}/${artifactId}/permissions`,
    );
    return data.members ?? [];
  },

  // The share sidebar toasts share/unshare failures itself.
  async share(artifactId: string, userIds: string[]): Promise<void> {
    await apiClient.post(`${BASE_URL}/${artifactId}/share`, { userIds }, { suppressErrorToast: true });
  },

  async unshare(artifactId: string, userIds: string[]): Promise<void> {
    await apiClient.post(`${BASE_URL}/${artifactId}/unshare`, { userIds }, { suppressErrorToast: true });
  },

  async delete(artifactId: string): Promise<void> {
    // 404 means it is already gone, which the caller treats as success.
    await apiClient.delete(`${BASE_URL}/${encodeURIComponent(artifactId)}`, {
      suppressErrorToast: (error) => error.statusCode === 404,
    });
  },
};
