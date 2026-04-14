import { apiClient } from '@/lib/api';
import { GROUP_TYPES, USER_ROLES } from '../constants';
import type { User, UsersListResponse, WithGroupsUser } from './types';

/**
 * Get all groups for a specific user by their MongoDB _id.
 * Calls the fetch/with-groups endpoint (same source used when building the Users table).
 * Returns the raw groups array — callers decide which group types to display or use for
 * role derivation.
 *
 * TODO: Replace with a dedicated GET /api/v1/users/{userId}/groups endpoint once available.
 * Current implementation downloads all users to find one — O(n) on network payload.
 */
export async function getUserGroupsForProfile(
  mongoId: string
): Promise<Array<{ name: string; type: string }>> {
  const users = await UsersApi.fetchUsersWithGroups();
  const user = users.find((u) => u._id === mongoId);
  return user?.groups ?? [];
}

const BASE_URL = '/api/v1/users';

export const UsersApi = {
  /**
   * List users with pagination from the graph API.
   * GET /api/v1/users/graph/list
   */
  async listUsers(params?: {
    page?: number;
    limit?: number;
    search?: string;
  }): Promise<{ users: User[]; totalCount: number }> {
    const { data } = await apiClient.get<UsersListResponse>(
      `${BASE_URL}/graph/list`,
      { params }
    );
    return {
      users: data.users ?? [],
      totalCount: data.pagination?.total ?? data.users?.length ?? 0,
    };
  },

  /**
   * Fetch users with their group memberships and hasLoggedIn status.
   * GET /api/v1/users/fetch/with-groups
   */
  async fetchUsersWithGroups(): Promise<WithGroupsUser[]> {
    const { data } = await apiClient.get(`${BASE_URL}/fetch/with-groups`);
    // Response may be a direct array or wrapped in an object
    return Array.isArray(data) ? data : (data as { users: WithGroupsUser[] }).users ?? [];
  },

  /**
   * Fetch merged users: server-paginates via graph/list, enriches with with-groups.
   * - graph/list  → id (UUID), email, timestamps, server pagination
   * - with-groups → hasLoggedIn, inline groups array (role + raw count)
   */
  async fetchMergedUsers(params?: {
    page?: number;
    limit?: number;
    search?: string;
  }): Promise<{ users: User[]; totalCount: number }> {
    const [graphResult, withGroupsUsers] = await Promise.all([
      UsersApi.listUsers(params),
      UsersApi.fetchUsersWithGroups(),
    ]);

    // Build lookup from MongoDB _id → with-groups user
    const wgByUserId = new Map<string, WithGroupsUser>();
    for (const wgUser of withGroupsUsers) {
      wgByUserId.set(wgUser._id, wgUser);
    }

    const mergedUsers: User[] = graphResult.users.map((user) => {
      const wgUser = wgByUserId.get(user.userId);
      const hasLoggedIn = wgUser?.hasLoggedIn ?? true;
      const groups = wgUser?.groups ?? [];

      return {
        ...user,
        name: user.name || wgUser?.fullName,
        hasLoggedIn,
        isActive: user.isActive ?? hasLoggedIn,
        // Pending users have no meaningful timestamps — omit so UI renders "-"
        createdAtTimestamp: hasLoggedIn ? user.createdAtTimestamp : undefined,
        updatedAtTimestamp: hasLoggedIn ? user.updatedAtTimestamp : undefined,
        role: groups.some((g) => g.type === GROUP_TYPES.ADMIN) ? USER_ROLES.ADMIN : USER_ROLES.MEMBER,
        // Use the raw groups array size, excluding the "everyone" system group
        groupCount: groups.filter((g) => g.type !== GROUP_TYPES.EVERYONE).length,
        userGroups: groups,
      };
    });

    return { users: mergedUsers, totalCount: graphResult.totalCount };
  },

  /**
   * Get a single user by ID.
   * GET /api/v1/users/:id
   */
  async getUser(id: string): Promise<User> {
    const { data } = await apiClient.get<User>(`${BASE_URL}/${id}`);
    return data;
  },

  /**
   * Invite users by email, optionally adding them to groups.
   * POST /api/v1/users/bulk/invite
   */
  async inviteUsers(emails: string[], groupIds?: string[]): Promise<void> {
    const payload: { emails: string[]; groupIds?: string[] } = { emails };
    if (groupIds && groupIds.length > 0) {
      payload.groupIds = groupIds;
    }
    await apiClient.post(`${BASE_URL}/bulk/invite`, payload);
  },

  /**
   * Delete (remove) a user from the workspace.
   * DELETE /api/v1/users/:userId
   */
  async deleteUser(userId: string): Promise<void> {
    await apiClient.delete(`${BASE_URL}/${userId}`);
  },

  /**
   * Resend an invite to a pending user.
   * POST /api/v1/users/:userId/resend-invite
   */
  async resendInvite(userId: string): Promise<void> {
    await apiClient.post(`${BASE_URL}/${userId}/resend-invite`);
  },

  // ── Bulk operations ───────────────────────────────────────────

  /**
   * Remove multiple users from the workspace in parallel.
   * Uses Promise.allSettled so partial failures don't block others.
   */
  async bulkRemoveUsers(
    userIds: string[]
  ): Promise<{ succeeded: number; failed: number }> {
    const results = await Promise.allSettled(
      userIds.map((id) => apiClient.delete(`${BASE_URL}/${id}`))
    );
    const succeeded = results.filter((r) => r.status === 'fulfilled').length;
    return { succeeded, failed: results.length - succeeded };
  },

  /**
   * Resend invites to multiple pending users in parallel.
   */
  async bulkResendInvites(
    userIds: string[]
  ): Promise<{ succeeded: number; failed: number }> {
    const results = await Promise.allSettled(
      userIds.map((id) => apiClient.post(`${BASE_URL}/${id}/resend-invite`))
    );
    const succeeded = results.filter((r) => r.status === 'fulfilled').length;
    return { succeeded, failed: results.length - succeeded };
  },

  /**
   * Cancel invites for multiple pending users in parallel.
   * Cancelling an invite is effectively deleting the pending user.
   */
  async bulkCancelInvites(
    userIds: string[]
  ): Promise<{ succeeded: number; failed: number }> {
    const results = await Promise.allSettled(
      userIds.map((id) => apiClient.delete(`${BASE_URL}/${id}`))
    );
    const succeeded = results.filter((r) => r.status === 'fulfilled').length;
    return { succeeded, failed: results.length - succeeded };
  },
};
