import { apiClient } from '@/lib/api';
import { UsersApi } from '@/app/(main)/workspace/users/api';
import type { User } from '@/app/(main)/workspace/users/types';
import { fetchShareUsersPaginated } from '@/app/components/share/utils';
import { ShareCommonApi } from '@/app/components/share/api';
import type { ShareAdapter, SharedMember, ShareSubmission, ShareRole, ShareUser } from '@/app/components/share/types';
import { useAuthStore } from '@/config';
import { useUserStore } from '@/lib/store/user-store';
import { AgentsApi } from '@/app/(main)/agents/api';
import { TeamsApi } from '@/app/(main)/workspace/teams/api';
import i18next from 'i18next';
import type { SharedWithEntry } from './types';
import { ChatApi } from './api';
import { useChatStore } from './store';
import { ProjectApi } from './project-api';
import type { ProjectDetail, ProjectMemberRole } from './project-types';

/** What `POST …/share` and `…/unshare` answer with. */
interface SharingAnswer {
  sharedWith?: SharedWithEntry[];
}

/** The open slot's copy first: it is the one the header shows. */
function knownSharedWith(conversationId: string): SharedWithEntry[] | null {
  const { activeSlotId, slots } = useChatStore.getState();
  const active = activeSlotId ? slots[activeSlotId] : undefined;
  if (active?.convId === conversationId) return active.sharedWith;
  return Object.values(slots).find((slot) => slot.convId === conversationId)?.sharedWith ?? null;
}

/** Every slot of the conversation, so whichever one is shown stays current. */
function rememberSharedWith(conversationId: string, sharedWith: SharedWithEntry[] | undefined): void {
  const { slots, updateSlot } = useChatStore.getState();
  for (const [slotId, slot] of Object.entries(slots)) {
    if (slot.convId === conversationId) updateSlot(slotId, { sharedWith: sharedWith ?? null });
  }
}

/**
 * Member rows for a chat: the owner, then one row per shared user. Only the
 * owner can see or change sharing, so the owner is the signed-in user.
 */
export async function toChatSharedMembers(sharedWith: SharedWithEntry[]): Promise<SharedMember[]> {
  // Read lazily from UserStore — populated by UserProfileInitializer on every
  // page load. useAuthStore.user is only set during the login flow itself and
  // is null after a refresh, so it is not a reliable source here.
  const profile = useUserStore.getState().profile;
  const ownerId = profile?.userId ?? '';
  const sharedIds = Array.from(new Set(sharedWith.map((entry) => entry.userId))).filter((id) => id !== ownerId);

  // Batch lookup by MongoDB userId: shared users need not be on the first page of the org.
  let users: User[] = [];
  if (sharedIds.length > 0) {
    try {
      users = await UsersApi.getUsersByIds(sharedIds);
    } catch {
      // Fallback: show IDs only
    }
  }
  const userMap = new Map<string, User>(users.map((u) => [u.userId, u]));
  const accessMap = new Map(sharedWith.map((entry) => [entry.userId, entry.accessLevel]));

  const members: SharedMember[] = [];
  if (ownerId) {
    members.push({
      id: ownerId,
      type: 'user',
      name: profile?.fullName?.trim() || profile?.email || 'Owner',
      email: profile?.email ?? undefined,
      avatarUrl: profile?.avatarUrl ?? undefined,
      role: 'OWNER',
      isOwner: true,
      isCurrentUser: true,
    });
  }
  for (const userId of sharedIds) {
    const userData = userMap.get(userId);
    members.push({
      id: userId,
      type: 'user',
      name: userData?.name ?? userData?.email ?? userId,
      email: userData?.email,
      avatarUrl: userData?.profilePicture,
      role: accessMap.get(userId) === 'write' ? 'WRITER' : 'READER',
      isOwner: false,
      isCurrentUser: false,
    });
  }
  return members;
}

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

  async function loadSharedWith(): Promise<SharedWithEntry[]> {
    const known = knownSharedWith(conversationId);
    if (known) return known;
    // Not known after a share answer that left `sharedWith` out; the smallest page still carries it.
    const { conversation } = agentId
      ? await AgentsApi.fetchAgentConversation(agentId, conversationId, { page: 1, limit: 1 })
      : await ChatApi.fetchConversation(conversationId, 1, 1);
    rememberSharedWith(conversationId, conversation.sharedWith);
    return conversation.sharedWith ?? [];
  }

  return {
    entityType: 'conversation',
    entityId: conversationId,
    sidebarTitle: 'Share Chat',
    supportsRoles: false,
    supportsTeams: false,

    async getSharedMembers(): Promise<SharedMember[]> {
      return toChatSharedMembers(await loadSharedWith());
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

      const { data } = await apiClient.post<SharingAnswer>(`${conversationBasePath}/share`, {
        userIds: Array.from(userIdsSet),
        accessLevel,
      });
      rememberSharedWith(conversationId, data?.sharedWith);
    },

    async removeMember(memberId: string): Promise<void> {
      const { data } = await apiClient.post<SharingAnswer>(`${conversationBasePath}/unshare`, {
        userIds: [memberId],
      });
      rememberSharedWith(conversationId, data?.sharedWith);
    },

    /** Paginated org users (Mongo userId) for chat /share. */
    getSharingUsersPaginated: fetchShareUsersPaginated,

    // No updateRole — supportsRoles is false
  };
}

/**
 * Project members only have `editor`/`viewer` roles (see `ProjectMemberRole`)
 * — ownership is a single `userId` field, not a member row, and isn't
 * reassignable from this dialog — so the picker offers only those two.
 */
function toProjectRole(role: ShareRole): ProjectMemberRole {
  return role === 'READER' ? 'viewer' : 'editor';
}

function toShareRole(role: ProjectMemberRole): ShareRole {
  return role === 'editor' ? 'WRITER' : 'READER';
}

export function projectShareRoleLabels(): Record<ShareRole, { label: string; description: string }> {
  return {
    OWNER: {
      label: i18next.t('chat.projects.shareRoles.owner.label'),
      description: i18next.t('chat.projects.shareRoles.owner.description'),
    },
    WRITER: {
      label: i18next.t('chat.projects.shareRoles.editor.label'),
      description: i18next.t('chat.projects.shareRoles.editor.description'),
    },
    READER: {
      label: i18next.t('chat.projects.shareRoles.viewer.label'),
      description: i18next.t('chat.projects.shareRoles.viewer.description'),
    },
  };
}

/** The org-wide team every member belongs to has a fixed id (`all_<orgId>`). */
function isAllMembersTeam(teamId: string): boolean {
  return teamId.startsWith('all_');
}

/** Team names for member rows: the caller's own teams first, then a lookup per remaining id. */
async function resolveTeamNames(teamIds: string[]): Promise<Map<string, { name: string; memberCount?: number }>> {
  const names = new Map<string, { name: string; memberCount?: number }>();
  if (teamIds.length === 0) return names;
  try {
    const mine = await ShareCommonApi.listUserTeams({ limit: 100 });
    mine.forEach((team) => names.set(team.id, { name: team.name, memberCount: team.memberCount }));
  } catch {
    // Fall through to per-team lookups.
  }
  await Promise.all(
    teamIds
      .filter((id) => !names.has(id) && !isAllMembersTeam(id))
      .map(async (id) => {
        try {
          const team = await TeamsApi.getTeam(id);
          if (team?.name) names.set(id, { name: team.name, memberCount: team.memberCount });
        } catch {
          // Leave unresolved; the row falls back to a generic label.
        }
      }),
  );
  return names;
}

/**
 * Creates a ShareAdapter for a Project. Only the owner can call `share`/
 * `updateRole`/`removeMember` (enforced server-side by
 * `ProjectService.upsertMembers`/`removeMember`); callers should gate the
 * "Share" entry point on `project.role === 'owner'`.
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
    supportsTeams: true,
    teamsHaveRoles: true,
    confirmBeforeRemove: true,
    roleOptions: { roles: ['WRITER', 'READER'], labels: projectShareRoleLabels() },

    async getSharedMembers(): Promise<SharedMember[]> {
      const members = await ProjectApi.listMembers(projectId);
      const userMembers = members.filter((m) => m.principalType === 'user');
      const teamMembers = members.filter((m) => m.principalType === 'team');
      // The members API names user rows itself; only look up the ones it couldn't.
      const unnamed = userMembers.filter((m) => !m.name && !m.email).map((m) => m.principalId);
      const [users, teamNames] = await Promise.all([
        ShareCommonApi.getUsersByIds(Array.from(new Set([ownerId, ...unnamed]))).catch((): ShareUser[] => []),
        resolveTeamNames(teamMembers.map((m) => m.principalId)),
      ]);
      const byId = new Map(users.map((u) => [u.id, u]));
      const unknown = i18next.t('chat.projects.unknownMember');

      const ownerInfo = byId.get(ownerId);
      const rows: SharedMember[] = [
        {
          id: ownerId,
          type: 'user',
          name: ownerInfo?.name || ownerInfo?.email || unknown,
          email: ownerInfo?.email,
          avatarUrl: ownerInfo?.avatarUrl,
          role: 'OWNER',
          isOwner: true,
          isCurrentUser: ownerId === currentUserId,
        },
      ];

      teamMembers.forEach((m) => {
        const team = teamNames.get(m.principalId);
        rows.push({
          id: m.principalId,
          type: 'team',
          name: isAllMembersTeam(m.principalId)
            ? i18next.t('chat.projects.allMembersTeam')
            : team?.name || i18next.t('chat.projects.unknownTeam'),
          memberCount: team?.memberCount,
          role: toShareRole(m.role),
          isOwner: false,
          isCurrentUser: false,
        });
      });

      userMembers.forEach((m) => {
        const info = byId.get(m.principalId);
        rows.push({
          id: m.principalId,
          type: 'user',
          name: m.name || info?.name || m.email || info?.email || unknown,
          email: m.email ?? info?.email,
          avatarUrl: info?.avatarUrl,
          role: toShareRole(m.role),
          isOwner: false,
          isCurrentUser: m.principalId === currentUserId,
        });
      });

      return rows;
    },

    async share(submission: ShareSubmission): Promise<void> {
      const role = toProjectRole(submission.role);
      const members = [
        ...submission.userIds.map((id) => ({ principalId: id, principalType: 'user' as const, role })),
        ...submission.teamIds.map((id) => ({ principalId: id, principalType: 'team' as const, role })),
      ];
      if (members.length === 0) return;
      await ProjectApi.upsertMembers(projectId, members);
    },

    async updateRole(memberId: string, memberType: 'user' | 'team', newRole: ShareRole): Promise<void> {
      if (memberType === 'user' && memberId === ownerId) {
        throw new Error(i18next.t('chat.projects.ownerCannotChange'));
      }
      await ProjectApi.upsertMembers(projectId, [
        { principalId: memberId, principalType: memberType, role: toProjectRole(newRole) },
      ]);
    },

    async removeMember(memberId: string, memberType: 'user' | 'team'): Promise<{ removed: boolean }> {
      if (memberType === 'user' && memberId === ownerId) {
        throw new Error(i18next.t('chat.projects.ownerCannotChange'));
      }
      const { removed } = await ProjectApi.removeMember(projectId, memberId, memberType);
      return { removed };
    },

    getSharingUsersPaginated: fetchShareUsersPaginated,
  };
}
