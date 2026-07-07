import type { ShareAdapter, SharedMember, ShareSubmission, ShareUser } from '@/app/components/share/types';
import { useAuthStore } from '@/lib/store/auth-store';
import { useUserStore } from '@/lib/store/user-store';
import { ConnectorsApi } from './api';
import { UsersApi } from '@/app/(main)/workspace/users/api';

/**
 * Creates a ShareAdapter for a personal connector instance.
 * Read-only shares only — supportsRoles is false, all grantees get READER.
 */
export function createConnectorShareAdapter(connectorId: string): ShareAdapter {
  // useAuthStore.user is only populated during the login flow itself and is
  // null after a refresh — useUserStore.profile is reliably populated on
  // every page load (decoded from the JWT). See chat/share-adapter.ts.
  const currentUserId = useUserStore.getState().profile?.userId ?? useAuthStore.getState().user?.id;

  return {
    entityType: 'search',
    entityId: connectorId,
    sidebarTitle: 'Share Connector',
    supportsRoles: false,
    supportsTeams: false, // v1: user sharing only; teams to be added in v2

    async getSharedMembers(): Promise<SharedMember[]> {
      const data = await ConnectorsApi.listConnectorShares(connectorId);
      const shares = Array.isArray(data.shares) ? data.shares : [];
      return shares.map((s) => {
        const isTeam = (s.type as string)?.toUpperCase() === 'TEAM';
        // Prefer MongoDB _id (userId) as the primary identifier so it is
        // consistent with what we send in share/revoke requests and with what
        // the auth store holds as user.id.  Fall back to graph _key.
        const memberId = s.userId ?? s.id;
        const isCurrentUser = currentUserId != null && memberId === currentUserId;
        return {
          id: memberId,
          type: (isTeam ? 'team' : 'user') as 'user' | 'team',
          name: s.name ?? s.email ?? 'Unknown',
          email: s.email,
          role: 'READER',
          isOwner: false,
          isCurrentUser,
        };
      });
    },

    async share(submission: ShareSubmission): Promise<void> {
      await ConnectorsApi.shareConnector(connectorId, {
        userIds: submission.userIds,
        teamIds: submission.teamIds,
      });
    },

    async removeMember(memberId: string, memberType: 'user' | 'team'): Promise<void> {
      const payload =
        memberType === 'user'
          ? { userIds: [memberId], teamIds: [] }
          : { userIds: [], teamIds: [memberId] };
      await ConnectorsApi.revokeConnectorShare(connectorId, payload);
    },

    async getSharingUsersPaginated(params: {
      page: number;
      limit: number;
      search?: string;
    }): Promise<{ users: ShareUser[]; totalCount: number }> {
      // Use the regular (MongoDB) users endpoint so that u.id = MongoDB _id,
      // which is the identifier the backend expects in userIds share payloads.
      const result = await UsersApi.listUsers({
        page: params.page,
        limit: params.limit,
        search: params.search,
      });
      return {
        users: result.users.map((u) => ({
          id: u.id,         // MongoDB _id — sent as userIds in share requests
          uuid: u.userId,   // same value, aliased for clarity
          name: u.name ?? u.email ?? '',
          email: u.email,
          isInOrg: true,
        })),
        totalCount: result.totalCount,
      };
    },
  };
}
