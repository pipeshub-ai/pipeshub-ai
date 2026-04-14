import { apiClient } from '@/lib/api';
import { UsersApi } from '@/app/(main)/workspace/users/api';
import type { User } from '@/app/(main)/workspace/users/types';
import type { ShareAdapter, SharedMember, ShareSubmission, ShareUser } from '@/app/components/share/types';
import { useAuthStore } from '@/lib/store/auth-store';
import { AgentsApi } from '@/app/(main)/agents/api';
import type { SharedWithEntry } from './types';

export interface CreateChatShareAdapterOptions {
  /** When set, uses GET/POST agent conversation share routes instead of global chat. */
  agentId?: string;
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
    supportsTeams: false,

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

      // Enrich with user details via fetchMergedUsers (keyed by MongoDB userId)
      let allUsers: User[] = [];
      try {
        const result = await UsersApi.fetchMergedUsers();
        allUsers = result.users;
      } catch {
        // Fallback: show IDs only
      }
      const userMap = new Map<string, User>(allUsers.map((u) => [u.userId, u]));

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
      await apiClient.post(`${conversationBasePath}/share`, {
        userIds: submission.userIds,
      });
    },

    async removeMember(memberId: string): Promise<void> {
      await apiClient.post(`${conversationBasePath}/unshare`, {
        userIds: [memberId],
      });
    },

    /**
     * Returns users with MongoDB ObjectIDs as id — required by the chat
     * /share endpoint. Overrides ShareCommonApi.getAllUsers() in the sidebar.
     */
    async getSharingUsers(): Promise<ShareUser[]> {
      let allUsers: User[] = [];
      try {
        const result = await UsersApi.fetchMergedUsers();
        allUsers = result.users;
      } catch {
        // Fallback: empty list
      }
      return allUsers.map((u) => ({
        id: u.userId,   // MongoDB ObjectID — what /share expects
        name: u.name ?? u.email ?? '',
        email: u.email,
        avatarUrl: undefined,
        isInOrg: true,
      }));
    },

    // No updateRole — supportsRoles is false
  };
}
