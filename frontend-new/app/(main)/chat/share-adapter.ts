import { apiClient } from '@/lib/api';
import { UsersApi } from '@/app/(main)/workspace/users/api';
import type { User } from '@/app/(main)/workspace/users/types';
import type { ShareAdapter, SharedMember, ShareSubmission, ShareUser } from '@/app/components/share/types';
import { useAuthStore } from '@/lib/store/auth-store';
import { AgentsApi } from '@/app/(main)/agents/api';
import { TeamsApi } from '@/app/(main)/workspace/teams/api';
import type { SharedWithEntry } from './types';

export interface CreateChatShareAdapterOptions {
  /** When set, uses GET/POST agent conversation share routes instead of global chat. */
  agentId?: string;
}

// Backend caps GET /api/v1/teams/:teamId/users at limit=100 per page.
const TEAM_MEMBERS_PAGE_LIMIT = 100;
// Safety cap — stops a runaway loop if totalCount is missing or wrong.
const TEAM_MEMBERS_MAX_PAGES = 50;

/**
 * Fetch every MongoDB userId for a team by paging through the members endpoint.
 * Returns the full set even for teams with more than one page of members.
 */
async function fetchAllTeamMemberIds(teamId: string): Promise<string[]> {
  const collected: string[] = [];
  let page = 1;

  // First page also gives us totalCount so we know whether to keep going.
  const first = await TeamsApi.getTeamUsers(teamId, {
    page,
    limit: TEAM_MEMBERS_PAGE_LIMIT,
  });
  for (const m of first.members) if (m.userId) collected.push(m.userId);

  const totalPages = Math.min(
    TEAM_MEMBERS_MAX_PAGES,
    Math.max(1, Math.ceil((first.totalCount ?? collected.length) / TEAM_MEMBERS_PAGE_LIMIT))
  );

  while (page < totalPages) {
    page += 1;
    const next = await TeamsApi.getTeamUsers(teamId, {
      page,
      limit: TEAM_MEMBERS_PAGE_LIMIT,
    });
    if (next.members.length === 0) break;
    for (const m of next.members) if (m.userId) collected.push(m.userId);
  }

  return collected;
}

/**
 * Creates a ShareAdapter for a Chat Conversation.
 * Simple share/unshare — no roles or teams.
 */
export function createChatShareAdapter(
  conversationId: string,
  options?: CreateChatShareAdapterOptions
): ShareAdapter {
  const currentUserId = useAuthStore.getState().user?.id;
  const agentId = options?.agentId;

  const conversationBasePath = agentId
    ? `/api/v1/agents/${agentId}/conversations/${conversationId}`
    : `/api/v1/conversations/${conversationId}`;

  return {
    entityType: 'conversation',
    entityId: conversationId,
    sidebarTitle: 'Share Chat',
    supportsRoles: false,
    supportsTeams: true,

    async getSharedMembers(): Promise<SharedMember[]> {
      let conversation: {
        sharedWith?: SharedWithEntry[];
        initiator?: string;
        userId?: string;
        ownerId?: string;
      };
      if (agentId) {
        conversation = (await AgentsApi.fetchAgentConversation(agentId, conversationId)).conversation;
      } else {
        const { data } = await apiClient.get(`/api/v1/conversations/${conversationId}/`);
        conversation = data.conversation ?? data;
      }
      const sharedWithEntries: SharedWithEntry[] = conversation.sharedWith ?? [];
      const sharedWithMongoIds = sharedWithEntries.map((entry: SharedWithEntry) => entry.userId);
      const ownerId: string = conversation.initiator ?? conversation.userId ?? conversation.ownerId ?? '';

      if (sharedWithMongoIds.length === 0 && !ownerId) return [];

      // Enrich with user details via batch-by-ids lookup (keyed by MongoDB userId).
      // This avoids the page-1-only cap from fetchMergedUsers when the conversation
      // is shared with users who don't appear in the first page of the org.
      const idsToLookup = Array.from(
        new Set([...sharedWithMongoIds, ...(ownerId ? [ownerId] : [])])
      );
      let enrichedUsers: User[] = [];
      try {
        enrichedUsers = await UsersApi.getUsersByIds(idsToLookup);
      } catch {
        // Fallback: show IDs only
      }
      const userMap = new Map<string, User>(enrichedUsers.map((u) => [u.userId, u]));

      // Build accessLevel lookup from sharedWith entries
      const accessMap = new Map(sharedWithEntries.map((entry: SharedWithEntry) => [entry.userId, entry.accessLevel]));
      const members: SharedMember[] = [];

      // Add owner
      if (ownerId) {
        const ownerData = userMap.get(ownerId);
        members.push({
          id: ownerId,
          type: 'user',
          name: ownerData?.name ?? ownerData?.email ?? 'Owner',
          email: ownerData?.email,
          avatarUrl: undefined,
          role: 'OWNER',
          isOwner: true,
          isCurrentUser: ownerId === currentUserId,
        });
      }

      // Add shared users
      for (const mongoId of sharedWithMongoIds) {
        if (mongoId === ownerId) continue;
        const userData = userMap.get(mongoId);
        const accessLevel = accessMap.get(mongoId) ?? 'read';
        members.push({
          id: mongoId,
          type: 'user',
          name: userData?.name ?? userData?.email ?? mongoId,
          email: userData?.email,
          avatarUrl: undefined,
          role: accessLevel === 'write' ? 'WRITER' : 'READER',
          isOwner: false,
          isCurrentUser: mongoId === currentUserId,
        });
      }

      return members;
    },

    async share(submission: ShareSubmission): Promise<void> {
      // Use a Set so dedupe against both the initial userIds and members pulled
      // from every selected team is O(1) per insertion.
      const userIdsSet = new Set(submission.userIds);

      // Expand team selections into individual MongoDB user IDs.
      // NOTE: the chat /share endpoint doesn't accept teamIds yet, so this
      // expansion is a frontend-side compatibility shim. Moving team expansion
      // behind the share endpoint would make this both more consistent and
      // avoid partial-share races — tracked as a backend follow-up.
      if (submission.teamIds && submission.teamIds.length > 0) {
        const teamMemberResults = await Promise.allSettled(
          submission.teamIds.map((teamId) => fetchAllTeamMemberIds(teamId))
        );
        for (const result of teamMemberResults) {
          if (result.status === 'fulfilled') {
            for (const mongoUserId of result.value) {
              userIdsSet.add(mongoUserId);
            }
          } else {
            // Log and continue: a single team failure shouldn't silently drop
            // the entire share — the user still gets the members we could
            // resolve from other teams and direct selections.
            console.error('Failed to fetch members for a selected team:', result.reason);
          }
        }
      }

      await apiClient.post(`${conversationBasePath}/share`, {
        userIds: Array.from(userIdsSet),
      });
    },

    async removeMember(memberId: string): Promise<void> {
      await apiClient.post(`${conversationBasePath}/unshare`, {
        userIds: [memberId],
      });
    },

    /**
     * Returns paginated users with MongoDB ObjectIDs as id — required by the chat
     * /share endpoint. Enables infinite scroll in the share sidebar.
     *
     * Uses listGraphUsers (GET /api/v1/users/graph/list), which returns both the
     * graph UUID (u.id) and the MongoDB ObjectId (u.userId). We expose MongoDB
     * as `id` for /share, and the graph UUID as `uuid` for team creation. The
     * plain /api/v1/users endpoint can't be used here: it sets both id and userId
     * to the MongoDB _id, leaving team creation with no valid UUID to submit.
     */
    async getSharingUsersPaginated(params: {
      page: number;
      limit: number;
      search?: string;
    }): Promise<{ users: ShareUser[]; totalCount: number }> {
      const result = await UsersApi.listGraphUsers({
        page: params.page,
        limit: params.limit,
        search: params.search,
      });
      return {
        users: result.users.map((u) => ({
          id: u.userId,   // MongoDB ObjectID — what /share expects
          uuid: u.id,     // Graph UUID — required by team creation
          name: u.name ?? u.email ?? '',
          email: u.email,
          avatarUrl: undefined,
          isInOrg: true,
        })),
        totalCount: result.totalCount,
      };
    },

    // No updateRole — supportsRoles is false
  };
}
