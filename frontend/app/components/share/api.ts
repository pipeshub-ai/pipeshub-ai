import { apiClient } from '@/lib/api';
import { UsersApi } from '@/app/(main)/workspace/users/api';
import { toShareUsers } from './utils';
import type { ShareTeam, ShareUser } from './types';

/**
 * Common APIs used by the share sidebar regardless of entity type.
 * Teams and users are shared resources across all entity types.
 *
 * Team CRUD lives in {@link '@/app/(main)/workspace/teams/api'.TeamsApi} — do
 * not duplicate it here.
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

  /**
   * First page of org users (Mongo userId). Used when an adapter has no paginated override.
   */
  async getAllUsers(): Promise<ShareUser[]> {
    const { users } = await UsersApi.fetchMergedUsers({ page: 1, limit: 100 });
    return toShareUsers(users);
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
      name: (u.name as string) ?? (u.fullName as string) ?? '',
      email: (u.email as string) ?? undefined,
      avatarUrl: (u.avatarUrl as string) || undefined,
      isInOrg: true,
    }));
  },
};
