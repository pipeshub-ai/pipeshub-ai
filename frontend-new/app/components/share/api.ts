import { apiClient } from '@/lib/api';
import type { CreateTeamPayload, ShareTeam, ShareUser } from './types';

/**
 * Common APIs used by the share sidebar regardless of entity type.
 * Teams and users are shared resources across all entity types.
 */
export const ShareCommonApi = {
  /** List teams the current user belongs to */
  async listUserTeams(params?: { page?: number; limit?: number; search?: string }): Promise<ShareTeam[]> {
    const { data } = await apiClient.get('/api/v1/teams/user/teams', { params });
    // Adapt shape if the API returns a wrapper
    const teams = Array.isArray(data) ? data : data.teams ?? [];
    return teams.map((t: Record<string, unknown>) => ({
      id: t.id as string,
      name: t.name as string,
      description: (t.description as string) ?? '',
      memberCount: (t.memberCount as number) ?? (t.members as unknown[] ?? []).length,
    }));
  },

  /** Create a new team */
  async createTeam(payload: CreateTeamPayload): Promise<ShareTeam> {
    const { data } = await apiClient.post('/api/v1/teams', payload, { suppressErrorToast: true });
    return {
      id: data.id as string,
      name: data.name,
      description: data.description ?? '',
      memberCount: data.memberCount ?? (data.members ?? []).length,
    };
  },

  /** Get a team by ID */
  async getTeamById(teamId: string): Promise<ShareTeam> {
    const { data } = await apiClient.get(`/api/v1/teams/${teamId}`);
    return {
      id: data.id as string,
      name: data.name,
      description: data.description ?? '',
      memberCount: data.memberCount ?? (data.members ?? []).length,
    };
  },

  /** Update an existing team */
  async updateTeam(teamId: string, payload: Partial<CreateTeamPayload>): Promise<ShareTeam> {
    const { data } = await apiClient.put(`/api/v1/teams/${teamId}`, payload);
    return {
      id: data.id as string,
      name: data.name,
      description: data.description ?? '',
      memberCount: data.memberCount ?? (data.members ?? []).length,
    };
  },

  /** Delete a team */
  async deleteTeam(teamId: string): Promise<void> {
    await apiClient.delete(`/api/v1/teams/${teamId}`);
  },

  /**
   * Get all org users. Returns UUID as id — used by endpoints that expect UUIDs
   * (e.g. collections/KB permissions). For MongoDB-ID endpoints use the
   * adapter's getSharingUsers() override instead.
   */
  async getAllUsers(): Promise<ShareUser[]> {
    const { data } = await apiClient.get('/api/v1/users/graph/list');
    const users = Array.isArray(data) ? data : data.users ?? [];
    return users.map((u: Record<string, unknown>) => ({
      id: u.id as string,
      uuid: u.id as string,
      name: (u.name as string) ?? '',
      email: (u.email as string) ?? undefined,
      avatarUrl: (u.avatarUrl as string) || undefined,
      isInOrg: true,
    }));
  },

  /**
   * Look up multiple users by UUID (batch lookup).
   * Used by adapters whose sharedWith IDs are UUIDs.
   */
  async getUsersByIds(userIds: string[]): Promise<ShareUser[]> {
    if (userIds.length === 0) return [];
    const { data } = await apiClient.post('/api/v1/users/by-ids', { userIds });
    const users = Array.isArray(data) ? data : data.users ?? [];
    return users.map((u: Record<string, unknown>) => ({
      id: u.id as string,
      uuid: u.id as string,
      name: (u.name as string) ?? (u.fullName as string) ?? '',
      email: (u.email as string) ?? undefined,
      avatarUrl: (u.avatarUrl as string) || undefined,
      isInOrg: true,
    }));
  },
};
