export const GALLERY_ARTIFACT_TYPES = [
  'IMAGE',
  'CHART',
  'DOCUMENT',
  'SPREADSHEET',
  'PRESENTATION',
  'DATA_FILE',
  'CODE',
  'CODE_OUTPUT',
  'OTHER',
] as const;

export type GalleryArtifactType = (typeof GALLERY_ARTIFACT_TYPES)[number];

export type ArtifactsSortField =
  | 'name'
  | 'createdAtTimestamp'
  | 'updatedAtTimestamp'
  | 'artifactType';

export type ArtifactsViewMode = 'list' | 'grid';

export const ARTIFACTS_OWNERSHIP_TABS = ['all', 'mine', 'shared'] as const;

/** Gallery tab: every artifact the user can read, the ones they own, or the ones shared with them. */
export type ArtifactsOwnership = (typeof ARTIFACTS_OWNERSHIP_TABS)[number];

export type ArtifactsOwnershipCounts = Record<ArtifactsOwnership, number>;

export type ArtifactRole = 'OWNER' | 'READER';

export interface ArtifactMember {
  /** Auth user id */
  userId: string;
  role: ArtifactRole;
}

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
  /** The agent whose chat produced this artifact; absent for assistant chats. */
  agentKey?: string;
  /** The project the chat belongs to, when the viewer can open that project. */
  projectId?: string;
  /** The chat is archived, so it opens from the archived chats page. */
  conversationArchived?: true;
  createdAt?: number | null;
  updatedAt?: number | null;
  /** The viewer's permission: OWNER for their own artifacts, READER for ones shared with them. */
  role?: ArtifactRole | null;
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
  counts?: ArtifactsOwnershipCounts;
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
  ownership?: ArtifactsOwnership;
}
