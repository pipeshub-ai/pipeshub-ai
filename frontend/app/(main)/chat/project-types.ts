import type { AppliedFilters } from './types';

export type ProjectVisibility = 'private' | 'org';
export type ProjectChatSharing = 'private' | 'members';
export type ProjectMemberRole = 'viewer' | 'editor';
export type ProjectPrincipalType = 'user' | 'team';
/** Effective role computed server-side for the requesting caller. */
export type ProjectRole = 'owner' | 'editor' | 'viewer' | 'none';
/** Per-chat override of a project's default chat sharing (mirrors `chatSessions.projectVisibility`). */
export type ProjectChatVisibility = 'private' | 'project';

/** Sentinel accepted by `?projectId=` on conversation-list endpoints to mean "no project". */
export const PROJECT_ID_UNASSIGNED = 'unassigned';

export interface ProjectKnowledgeScope {
  apps?: string[];
  kb?: string[];
}

export interface ProjectMember {
  principalType: ProjectPrincipalType;
  principalId: string;
  role: ProjectMemberRole;
  addedBy: string;
  addedAt: string;
}

export interface ProjectFileRef {
  recordId: string;
  recordName?: string;
  mimeType?: string;
  extension?: string;
  virtualRecordId?: string;
  source?: 'upload' | 'paste-text';
  sizeBytes?: number;
  uploadedBy: string;
  uploadedAt: string;
}

/** One row from `GET /api/v1/projects` — server-enriched with the caller's role and conversation count. */
export interface ProjectSummary {
  _id: string;
  orgId: string;
  userId: string;
  name: string;
  description?: string;
  icon?: string;
  color?: string;
  instructions?: string;
  knowledgeScope?: ProjectKnowledgeScope;
  appliedFilters?: AppliedFilters;
  visibility: ProjectVisibility;
  chatSharing: ProjectChatSharing;
  isPinned: boolean;
  isArchived: boolean;
  lastActivityAt: number;
  createdAt: string;
  updatedAt: string;
  role: ProjectRole;
  conversationCount: number;
}

/** `GET /api/v1/projects/:projectId` — full document incl. files/members, plus computed `role`. */
export interface ProjectDetail extends Omit<ProjectSummary, 'conversationCount'> {
  files: ProjectFileRef[];
  members: ProjectMember[];
}

export interface CreateProjectInput {
  name: string;
  description?: string;
  icon?: string;
  color?: string;
  instructions?: string;
  knowledgeScope?: ProjectKnowledgeScope;
  appliedFilters?: AppliedFilters;
}

export interface UpdateProjectInput {
  name?: string;
  description?: string;
  icon?: string;
  color?: string;
  instructions?: string;
  knowledgeScope?: ProjectKnowledgeScope;
  appliedFilters?: AppliedFilters;
  /** Owner-only. */
  visibility?: ProjectVisibility;
  /** Owner-only. */
  chatSharing?: ProjectChatSharing;
}

export type ProjectListScope = 'mine' | 'shared' | 'all';

export interface ListProjectsParams {
  page?: number;
  limit?: number;
  search?: string;
  scope?: ProjectListScope;
  includeArchived?: boolean;
}

export interface ProjectsListResult {
  projects: ProjectSummary[];
  pagination: {
    page: number;
    limit: number;
    totalCount: number;
    totalPages: number;
  };
}
