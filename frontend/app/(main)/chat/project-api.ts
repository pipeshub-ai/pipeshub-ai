import { apiClient } from '@/lib/api';
import type {
  CreateProjectInput,
  ListProjectsParams,
  ProjectDetail,
  ProjectMember,
  ProjectMemberRole,
  ProjectPrincipalType,
  ProjectsListResult,
  UpdateProjectInput,
} from './project-types';

const PROJECTS_BASE_URL = '/api/v1/projects';

/** One row from `GET /:projectId/conversations` — raw `chatSessions` fields (not the enriched `/conversations` shape). */
export interface ProjectConversationRow {
  _id: string;
  title?: string;
  sessionType: 'chat' | 'agent';
  agentKey?: string;
  userId: string;
  initiator: string;
  isShared: boolean;
  isArchived: boolean;
  status: string;
  lastActivityAt: number;
  projectVisibility?: 'private' | 'project';
  createdAt: string;
  updatedAt: string;
}

export interface ProjectConversationsResult {
  conversations: ProjectConversationRow[];
  pagination: {
    page: number;
    limit: number;
    totalCount: number;
    totalPages: number;
  };
}

export const ProjectApi = {
  async list(params: ListProjectsParams = {}): Promise<ProjectsListResult> {
    const { data } = await apiClient.get<ProjectsListResult>(PROJECTS_BASE_URL, {
      params: {
        page: params.page ?? 1,
        limit: params.limit ?? 20,
        ...(params.search ? { search: params.search } : {}),
        scope: params.scope ?? 'mine',
        ...(params.includeArchived !== undefined
          ? { includeArchived: params.includeArchived }
          : {}),
      },
    });
    return data;
  },

  async get(projectId: string): Promise<ProjectDetail> {
    const { data } = await apiClient.get<{ project: ProjectDetail }>(
      `${PROJECTS_BASE_URL}/${encodeURIComponent(projectId)}`,
    );
    return data.project;
  },

  async create(input: CreateProjectInput): Promise<ProjectDetail> {
    const { data } = await apiClient.post<{ project: ProjectDetail }>(
      PROJECTS_BASE_URL,
      input,
    );
    return data.project;
  },

  async update(projectId: string, patch: UpdateProjectInput): Promise<ProjectDetail> {
    const { data } = await apiClient.patch<{ project: ProjectDetail }>(
      `${PROJECTS_BASE_URL}/${encodeURIComponent(projectId)}`,
      patch,
    );
    return data.project;
  },

  async remove(projectId: string): Promise<void> {
    await apiClient.delete(`${PROJECTS_BASE_URL}/${encodeURIComponent(projectId)}`);
  },

  async archive(projectId: string): Promise<ProjectDetail> {
    const { data } = await apiClient.post<{ project: ProjectDetail }>(
      `${PROJECTS_BASE_URL}/${encodeURIComponent(projectId)}/archive`,
    );
    return data.project;
  },

  async unarchive(projectId: string): Promise<ProjectDetail> {
    const { data } = await apiClient.post<{ project: ProjectDetail }>(
      `${PROJECTS_BASE_URL}/${encodeURIComponent(projectId)}/unarchive`,
    );
    return data.project;
  },

  async pin(projectId: string): Promise<ProjectDetail> {
    const { data } = await apiClient.post<{ project: ProjectDetail }>(
      `${PROJECTS_BASE_URL}/${encodeURIComponent(projectId)}/pin`,
    );
    return data.project;
  },

  async unpin(projectId: string): Promise<ProjectDetail> {
    const { data } = await apiClient.post<{ project: ProjectDetail }>(
      `${PROJECTS_BASE_URL}/${encodeURIComponent(projectId)}/unpin`,
    );
    return data.project;
  },

  async listConversations(
    projectId: string,
    params: { page?: number; limit?: number } = {},
  ): Promise<ProjectConversationsResult> {
    const { data } = await apiClient.get<ProjectConversationsResult>(
      `${PROJECTS_BASE_URL}/${encodeURIComponent(projectId)}/conversations`,
      { params: { page: params.page ?? 1, limit: params.limit ?? 20 } },
    );
    return data;
  },

  /** Lazily creates (idempotent) the project's hidden linked Collection and returns its id — call before the first upload. */
  async ensureKnowledgeBase(projectId: string): Promise<string> {
    const { data } = await apiClient.post<{ kbId: string }>(
      `${PROJECTS_BASE_URL}/${encodeURIComponent(projectId)}/knowledge-base`,
    );
    return data.kbId;
  },

  async listMembers(projectId: string): Promise<ProjectMember[]> {
    const { data } = await apiClient.get<{ members: ProjectMember[] }>(
      `${PROJECTS_BASE_URL}/${encodeURIComponent(projectId)}/members`,
    );
    return data.members ?? [];
  },

  async upsertMembers(
    projectId: string,
    members: Array<{
      principalId: string;
      principalType?: ProjectPrincipalType;
      role: ProjectMemberRole;
    }>,
  ): Promise<ProjectMember[]> {
    const { data } = await apiClient.put<{ members: ProjectMember[] }>(
      `${PROJECTS_BASE_URL}/${encodeURIComponent(projectId)}/members`,
      { members },
    );
    return data.members ?? [];
  },

  async removeMember(
    projectId: string,
    memberPrincipalId: string,
    principalType: ProjectPrincipalType = 'user',
  ): Promise<ProjectMember[]> {
    const { data } = await apiClient.delete<{ members: ProjectMember[] }>(
      `${PROJECTS_BASE_URL}/${encodeURIComponent(projectId)}/members/${encodeURIComponent(memberPrincipalId)}`,
      { params: { principalType } },
    );
    return data.members ?? [];
  },

  async setConversationProject(
    conversationId: string,
    projectId: string | null,
    opts: { agentKey?: string } = {},
  ): Promise<void> {
    const base = opts.agentKey
      ? `/api/v1/agents/${encodeURIComponent(opts.agentKey)}/conversations`
      : '/api/v1/conversations';
    await apiClient.put(`${base}/${encodeURIComponent(conversationId)}/project`, {
      projectId,
    });
  },

  async setConversationProjectVisibility(
    conversationId: string,
    visibility: 'private' | 'project',
    opts: { agentKey?: string } = {},
  ): Promise<void> {
    const base = opts.agentKey
      ? `/api/v1/agents/${encodeURIComponent(opts.agentKey)}/conversations`
      : '/api/v1/conversations';
    await apiClient.patch(`${base}/${encodeURIComponent(conversationId)}/project-visibility`, {
      visibility,
    });
  },
};
