import { apiClient } from '@/lib/api';
import { UsersApi } from '@/app/(main)/workspace/users/api';
import type { User } from '@/app/(main)/workspace/users/types';
import { fetchShareUsersPaginated } from '@/app/components/share/utils';
import { ShareCommonApi } from '@/app/components/share/api';
import type { ShareAdapter, SharedMember, ShareSubmission, ShareRole } from '@/app/components/share/types';
import { useAuthStore } from '@/config';
import { useUserStore } from '@/lib/store/user-store';
import { AgentsApi } from '@/app/(main)/agents/api';
import i18next from 'i18next';
import type { SharedWithEntry } from './types';
import { ProjectApi } from './project-api';
import type { ProjectDetail, ProjectMemberRole } from './project-types';

export interface CreateChatShareAdapterOptions {
  /** When set, uses GET/POST agent conversation share routes instead of global chat. */
  agentId?: string;
}

/**
 * Creates a ShareAdapter for a Chat Conversation.
 * Simple share/unshare — no roles and no team-sharing UI.
 */
export function createChatShareAdapter(
  conversationId: string,
  options?: CreateChatShareAdapterOptions
): ShareAdapter {
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
      // Read lazily from UserStore — populated by UserProfileInitializer on every
      // page load. useAuthStore.user is only set during the login flow itself and
      // is null after a refresh, so it is not a reliable source here.
      const currentUserId = useUserStore.getState().profile?.userId;

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
      let users: User[] = [];
      try {
        users = await UsersApi.getUsersByIds(idsToLookup);
      } catch {
        // Fallback: show IDs only
      }
      const userMap = new Map<string, User>(users.map((u) => [u.userId, u]));

      // Build accessLevel lookup from sharedWith entries
      const accessMap = new Map(sharedWithEntries.map((entry: SharedWithEntry) => [entry.userId, entry.accessLevel]));
      const members: SharedMember[] = [];

      // Add owner
      if (ownerId) {
        const ownerData = userMap.get(ownerId.toString());
        members.push({
          id: ownerId.toString(),
          type: 'user',
          name: ownerData?.name ?? ownerData?.email ?? 'Owner',
          email: ownerData?.email,
          avatarUrl: ownerData?.profilePicture,
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
          avatarUrl: userData?.profilePicture,
          role: accessLevel === 'write' ? 'WRITER' : 'READER',
          isOwner: false,
          isCurrentUser: mongoId === currentUserId,
        });
      }

      return members;
    },

    async share(submission: ShareSubmission): Promise<void> {
      // Chat share currently supports direct-user IDs only.
      const userIdsSet = new Set(submission.userIds);

      // The chat /share endpoint accepts an optional accessLevel ('read' | 'write').
      // The sidebar forces submission.role to 'READER' when supportsRoles is false
      // (our case), but map it explicitly so the backend contract is respected if
      // the adapter is ever flipped to support roles.
      const accessLevel: 'read' | 'write' =
        submission.role === 'WRITER' ? 'write' : 'read';

      await apiClient.post(`${conversationBasePath}/share`, {
        userIds: Array.from(userIdsSet),
        accessLevel,
      });
    },

    async removeMember(memberId: string): Promise<void> {
      await apiClient.post(`${conversationBasePath}/unshare`, {
        userIds: [memberId],
      });
    },

    /** Paginated org users (Mongo userId) for chat /share. */
    getSharingUsersPaginated: fetchShareUsersPaginated,

    // No updateRole — supportsRoles is false
  };
}

/**
 * Project members only have `editor`/`viewer` roles (see `ProjectMemberRole`)
 * — ownership is a single `userId` field, not a member row, and isn't
 * reassignable from this dialog. The generic `RoleDropdownMenu` always offers
 * OWNER/WRITER/READER, so an `OWNER` selection here is clamped to `editor`
 * (the highest role the project members API accepts).
 */
function toProjectRole(role: ShareRole): ProjectMemberRole {
  return role === 'READER' ? 'viewer' : 'editor';
}

function toShareRole(role: ProjectMemberRole): ShareRole {
  return role === 'editor' ? 'WRITER' : 'READER';
}

/**
 * Creates a ShareAdapter for a Project. Only the owner can call `share`/
 * `updateRole`/`removeMember` (enforced server-side by
 * `ProjectService.upsertMembers`/`removeMember`); callers should gate the
 * "Share" entry point on `project.role === 'owner'`.
 *
 * Team principals are deferred (see the plan's "Deferred" section) — any
 * `principalType: 'team'` member rows are hidden here rather than surfaced
 * half-supported.
 */
export function createProjectShareAdapter(project: ProjectDetail): ShareAdapter {
  const profile = useUserStore.getState().profile;
  const authUser = useAuthStore.getState().user;
  const currentUserId = (profile?.userId ?? authUser?.id ?? '').trim();
  const projectId = project._id;
  const ownerId = project.userId;

  return {
    entityType: 'project',
    entityId: projectId,
    sidebarTitle: i18next.t('chat.projects.shareProject'),
    supportsRoles: true,
    supportsTeams: false,

    async getSharedMembers(): Promise<SharedMember[]> {
      const members = await ProjectApi.listMembers(projectId);
      const userMembers = members.filter((m) => m.principalType === 'user');
      const ids = Array.from(new Set([ownerId, ...userMembers.map((m) => m.principalId)]));
      const users = await ShareCommonApi.getUsersByIds(ids);
      const byId = new Map(users.map((u) => [u.id, u]));

      const ownerInfo = byId.get(ownerId);
      const rows: SharedMember[] = [
        {
          id: ownerId,
          type: 'user',
          name: ownerInfo?.name ?? 'Unknown',
          email: ownerInfo?.email,
          avatarUrl: ownerInfo?.avatarUrl,
          role: 'OWNER',
          isOwner: true,
          isCurrentUser: ownerId === currentUserId,
        },
      ];

      userMembers.forEach((m) => {
        const info = byId.get(m.principalId);
        rows.push({
          id: m.principalId,
          type: 'user',
          name: info?.name ?? 'Unknown',
          email: info?.email,
          avatarUrl: info?.avatarUrl,
          role: toShareRole(m.role),
          isOwner: false,
          isCurrentUser: m.principalId === currentUserId,
        });
      });

      return rows;
    },

    async share(submission: ShareSubmission): Promise<void> {
      if (submission.userIds.length === 0) return;
      await ProjectApi.upsertMembers(
        projectId,
        submission.userIds.map((id) => ({ principalId: id, role: toProjectRole(submission.role) })),
      );
    },

    async updateRole(memberId: string, memberType: 'user' | 'team', newRole: ShareRole): Promise<void> {
      if (memberType !== 'user') return;
      if (memberId === ownerId) {
        throw new Error('The project owner cannot be reassigned here.');
      }
      await ProjectApi.upsertMembers(projectId, [
        { principalId: memberId, role: toProjectRole(newRole) },
      ]);
    },

    async removeMember(memberId: string, memberType: 'user' | 'team'): Promise<void> {
      if (memberType !== 'user') return;
      if (memberId === ownerId) {
        throw new Error('The project owner cannot be removed.');
      }
      await ProjectApi.removeMember(projectId, memberId);
    },

    getSharingUsersPaginated: fetchShareUsersPaginated,
  };
}
