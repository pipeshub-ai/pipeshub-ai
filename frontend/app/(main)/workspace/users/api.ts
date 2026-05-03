import { apiClient } from '@/lib/api';
import type {
  GraphUsersListResponse,
  User,
  UserByIdsDoc,
  UsersListResponse,
  WithGroupsUser,
} from './types';

function mongoIdToString(id: UserByIdsDoc['_id']): string {
  return typeof id === 'string' ? id : id.toString();
}

/** Maps POST /by-ids payload into the shared {@link User} shape used across the app. */
function userFromByIdsDoc(doc: UserByIdsDoc): User {
  const userId = mongoIdToString(doc._id);
  return {
    id: userId,
    userId,
    name: doc.fullName,
    email: doc.email,
    hasLoggedIn: doc.hasLoggedIn ?? false,
    // Not merged with block list here; treat as active org member for display lookups.
    isActive: true,
  };
}

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
   * List users with pagination and server-side filters.
   * GET /api/v1/users?page=&limit=&search=&hasLoggedIn=&groupIds=
   * Queries MongoDB directly with filters applied at the database level.
   */
  async listUsers(params?: {
    page?: number;
    limit?: number;
    search?: string;
    hasLoggedIn?: string;
    isBlocked?: string;
    groupIds?: string;
  }): Promise<{ users: User[]; totalCount: number }> {
    const { data } = await apiClient.get<UsersListResponse>(
      BASE_URL,
      { params }
    );
    return {
      users: data.users ?? [],
      totalCount: data.pagination?.totalCount ?? data.users?.length ?? 0,
    };
  },

  /**
   * List users from the graph API (returns graph UUID as `id`).
   * Use this when you need the graph key that matches team.createdBy.
   * GET /api/v1/users/graph/list
   */
  async listGraphUsers(params?: {
    page?: number;
    limit?: number;
    search?: string;
  }): Promise<{ users: User[]; totalCount: number }> {
    const { data } = await apiClient.get<GraphUsersListResponse>(
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
    return Array.isArray(data) ? data : (data as { users: WithGroupsUser[] }).users ?? [];
  },

  /**
   * Unblock a user. PUT /api/v1/users/:userId/unblock (admin)
   */
  async unblockUser(userId: string): Promise<void> {
    await apiClient.put(`${BASE_URL}/${userId}/unblock`);
  },

  /**
   * Fetch users with all enrichment (groups, blocked status, profile pictures).
   * Single call to GET /api/v1/users — backend returns everything.
   */
  async fetchMergedUsers(params?: {
    page?: number;
    limit?: number;
    search?: string;
    hasLoggedIn?: string;
    isBlocked?: string;
    groupIds?: string;
  }): Promise<{ users: User[]; totalCount: number }> {
    return UsersApi.listUsers(params);
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
   * Resolve graph users by graph UUIDs using the graph list endpoint.
   * Paginates until all requested IDs are found (or data is exhausted).
   */
  async getGraphUsersByIds(ids: string[]): Promise<Record<string, User | null>> {
    const uniqueIds = Array.from(new Set(ids.filter(Boolean)));
    const result: Record<string, User | null> = {};
    for (const id of uniqueIds) result[id] = null;
    if (uniqueIds.length === 0) return result;

    const remaining = new Set(uniqueIds);
    let page = 1;
    const limit = 100;
    let totalPages = 1;

    while (page <= totalPages && remaining.size > 0) {
      const { users, totalCount } = await UsersApi.listGraphUsers({ page, limit });
      totalPages = Math.max(1, Math.ceil((totalCount || 0) / limit));

      for (const user of users) {
        if (!user.id || !remaining.has(user.id)) continue;
        result[user.id] = user;
        remaining.delete(user.id);
      }
      page += 1;
    }

    return result;
  },

  /**
   * Batch lookup users by their MongoDB IDs.
   * POST /api/v1/users/by-ids
   * Use this to enrich known user IDs with name/email without scanning
   * the whole user list.
   */
  async getUsersByIds(userIds: string[]): Promise<User[]> {
    if (userIds.length === 0) return [];
    const { data } = await apiClient.post<
      UserByIdsDoc[] | { users: UserByIdsDoc[] }
    >(`${BASE_URL}/by-ids`, { userIds });
    const raw = Array.isArray(data) ? data : data.users ?? [];
    return raw.map(userFromByIdsDoc);
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
