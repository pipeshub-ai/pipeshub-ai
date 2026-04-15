import { apiClient } from '@/lib/api';
import type { Group } from './types';

const BASE_URL = '/api/v1/userGroups';

export const GroupsApi = {
  /**
   * List all groups.
   * GET /api/v1/userGroups
   * Returns all groups — no server-side pagination.
   *
   * // TODO: Add server-side pagination when backend supports it
   */
  async listGroups(): Promise<Group[]> {
    const { data } = await apiClient.get<Group[]>(BASE_URL);
    return Array.isArray(data) ? data : [];
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
