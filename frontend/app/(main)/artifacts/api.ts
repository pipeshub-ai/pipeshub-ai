import { apiClient } from '@/lib/api';
import type {
  ArtifactDetail,
  ArtifactGalleryVersion,
  ArtifactListParams,
  ArtifactListResponse,
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
  return query;
}

export const ArtifactsApi = {
  async list(params: ArtifactListParams = {}): Promise<ArtifactListResponse> {
    const { data } = await apiClient.get<ArtifactListResponse>(BASE_URL, {
      params: toQuery(params),
    });
    return data;
  },

  async get(artifactId: string): Promise<ArtifactDetail> {
    const { data } = await apiClient.get<ArtifactDetail>(`${BASE_URL}/${artifactId}`);
    return data;
  },

  async listVersions(artifactId: string): Promise<ArtifactGalleryVersion[]> {
    const { data } = await apiClient.get<{ versions: ArtifactGalleryVersion[] }>(
      `${BASE_URL}/${artifactId}/versions`,
    );
    return data.versions ?? [];
  },
};
