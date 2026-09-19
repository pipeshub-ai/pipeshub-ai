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
  /** Tool fullNames (toolset + MCP) available to this project in agent mode. */
  tools: string[];
  /** The project's hidden Collection for uploaded files — null until the first upload creates it (see `ProjectApi.ensureKnowledgeBase`). */
  linkedKnowledgeBaseId?: string | null;
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

/** `GET /api/v1/projects/:projectId` — full document incl. members, plus computed `role`. Files live in the linked hidden Collection, fetched separately via the Knowledge Hub API. */
export interface ProjectDetail extends Omit<ProjectSummary, 'conversationCount'> {
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
  tools?: string[];
}

export interface UpdateProjectInput {
  name?: string;
  description?: string;
  icon?: string;
  color?: string;
  instructions?: string;
  knowledgeScope?: ProjectKnowledgeScope;
  appliedFilters?: AppliedFilters;
  tools?: string[];
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
