import i18next from 'i18next';
import { ShareCommonApi } from '@/app/components/share/api';
import { fetchShareUsersPaginated } from '@/app/components/share/utils';
import type { ShareAdapter, SharedMember, ShareSubmission } from '@/app/components/share/types';
import { useUserStore } from '@/lib/store/user-store';
import { ArtifactsApi } from './api';

/**
 * Share one artifact with people in the org: view and download only, so no
 * roles and no teams. Only the owner opens this; the server enforces it.
 */
export function createArtifactShareAdapter(artifactId: string): ShareAdapter {
  return {
    entityType: 'artifact',
    entityId: artifactId,
    sidebarTitle: i18next.t('artifacts.share.title'),
    supportsRoles: false,
    supportsTeams: false,

    async getSharedMembers(): Promise<SharedMember[]> {
      const currentUserId = useUserStore.getState().profile?.userId;
      const members = await ArtifactsApi.listMembers(artifactId);
      const users = await ShareCommonApi.getUsersByIds(members.map((m) => m.userId));
      const byId = new Map(users.map((u) => [u.id, u]));
      return members
        .map((m): SharedMember => {
          const user = byId.get(m.userId);
          return {
            id: m.userId,
            type: 'user',
            name: user?.name || user?.email || m.userId,
            email: user?.email,
            avatarUrl: user?.avatarUrl,
            role: m.role,
            isOwner: m.role === 'OWNER',
            isCurrentUser: m.userId === currentUserId,
          };
        })
        .sort((a, b) => Number(b.isOwner) - Number(a.isOwner));
    },

    async share({ userIds }: ShareSubmission): Promise<void> {
      if (userIds.length === 0) return;
      await ArtifactsApi.share(artifactId, userIds);
    },

    async removeMember(memberId: string): Promise<void> {
      await ArtifactsApi.unshare(artifactId, [memberId]);
    },

    getSharingUsersPaginated: fetchShareUsersPaginated,
  };
}
