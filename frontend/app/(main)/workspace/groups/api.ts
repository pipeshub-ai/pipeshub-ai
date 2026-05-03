import { apiClient } from '@/lib/api';
import type { Group, GroupsListResponse, GroupUsersResponse, GroupUser } from './types';

const BASE_URL = '/api/v1/userGroups';

export const GroupsApi = {
  /**
   * List groups with server-side pagination.
   * GET /api/v1/userGroups?page=&limit=&search=
   */
  async listGroups(params?: {
    page?: number;
    limit?: number;
    search?: string;
    createdAfter?: string;
    createdBefore?: string;
  }): Promise<{ groups: Group[]; totalCount: number }> {
    const { data } = await apiClient.get<GroupsListResponse>(BASE_URL, { params });
    return {
      groups: data.groups ?? [],
      totalCount: data.pagination?.totalCount ?? data.groups?.length ?? 0,
    };
  },

  /**
   * Get a single group by ID.
   * GET /api/v1/userGroups/:id
   */
  async getGroup(id: string): Promise<Group> {
    const { data } = await apiClient.get<Group>(`${BASE_URL}/${id}`);
    return data;
  },

  /**
   * Get users in a group with pagination and profile pictures.
   * GET /api/v1/userGroups/:groupId/users?page=&limit=&search=
   */
  async getGroupUsers(
    groupId: string,
    params?: { page?: number; limit?: number; search?: string }
  ): Promise<{ users: GroupUser[]; totalCount: number }> {
    const { data } = await apiClient.get<GroupUsersResponse>(
      `${BASE_URL}/${groupId}/users`,
      { params }
    );
    return {
      users: data.users ?? [],
      totalCount: data.pagination?.totalCount ?? data.users?.length ?? 0,
    };
  },

  /**
   * Get group stats (user counts).
   * GET /api/v1/userGroups/stats/list
   */
  async getGroupStats(): Promise<unknown> {
    const { data } = await apiClient.get(`${BASE_URL}/stats/list`);
    return data;
  },

  /**
   * Create a new group.
   * POST /api/v1/userGroups
   */
  async createGroup(name: string): Promise<Group> {
    const { data } = await apiClient.post<Group>(BASE_URL, {
      name,
      type: 'custom',
    });
    return data;
  },

  /**
   * Add users to one or more groups.
   * POST /api/v1/userGroups/add-users
   */
  async addUsersToGroups(
    userIds: string[],
    groupIds: string[]
  ): Promise<void> {
    await apiClient.post(`${BASE_URL}/add-users`, { userIds, groupIds });
  },

  /**
   * Remove users from one or more groups.
   * POST /api/v1/userGroups/remove-users
   */
  async removeUsersFromGroups(
    userIds: string[],
    groupIds: string[]
  ): Promise<void> {
    await apiClient.post(`${BASE_URL}/remove-users`, { userIds, groupIds });
  },

  /**
   * Delete a group.
   * DELETE /api/v1/userGroups/:id
   */
  async deleteGroup(id: string): Promise<void> {
    await apiClient.delete(`${BASE_URL}/${id}`);
  },
};
