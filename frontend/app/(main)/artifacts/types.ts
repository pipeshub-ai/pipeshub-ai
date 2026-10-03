export const GALLERY_ARTIFACT_TYPES = [
  'IMAGE',
  'CHART',
  'DOCUMENT',
  'SPREADSHEET',
  'PRESENTATION',
  'DATA_FILE',
  'CODE',
  'CODE_OUTPUT',
] as const;

export type GalleryArtifactType = (typeof GALLERY_ARTIFACT_TYPES)[number];

export type ArtifactsSortField =
  | 'name'
  | 'createdAtTimestamp'
  | 'updatedAtTimestamp'
  | 'artifactType';

export type ArtifactsViewMode = 'list' | 'grid';

export interface ArtifactGalleryVersion {
  version: number;
  sizeBytes: number;
  contentHash: string;
  createdAt: number;
}

export interface ArtifactListItem {
  artifactId: string;
  name: string;
  logicalName?: string | null;
  artifactType: string;
  mimeType?: string | null;
  sizeInBytes?: number | null;
  version: number;
  contentHash?: string | null;
  conversationId?: string | null;
  conversationTitle?: string;
  createdAt?: number | null;
  updatedAt?: number | null;
}

export interface ArtifactDetail extends ArtifactListItem {
  versions: ArtifactGalleryVersion[];
  description: string;
  sourceTool?: string | null;
}

export interface ArtifactListResponse {
  items: ArtifactListItem[];
  pagination: {
    page: number;
    limit: number;
    totalCount: number;
    totalPages: number;
  };
}

export interface ArtifactListParams {
  page?: number;
  limit?: number;
  search?: string;
  artifactTypes?: string[];
  conversationId?: string;
  dateFrom?: number;
  dateTo?: number;
  sortBy?: ArtifactsSortField;
  sortOrder?: 'asc' | 'desc';
}
