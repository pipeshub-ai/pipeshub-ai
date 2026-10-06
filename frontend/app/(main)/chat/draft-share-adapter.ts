import i18next from 'i18next';
import { fetchShareUsersPaginated } from '@/app/components/share/utils';
import type { ShareAdapter, ShareMode, SharedMember, ShareSelection, ShareSubmission } from '@/app/components/share/types';
import { useUserStore } from '@/lib/store/user-store';
import { COLLABORATORS_PER_PUT_MAX, NOTE_MAX_LENGTH, formatShareError, requiresShareConfirm } from './collab-share-adapter';
import { useDraftShareStore, type DraftPrincipal } from './draft-share-store';

const t = (key: string, options?: Record<string, unknown>) => i18next.t(key, options) as string;

const levelOf = (role: string): DraftPrincipal['level'] => (role === 'WRITER' ? 'write' : 'read');

/**
 * ShareAdapter for a chat that does not exist yet: nothing is sent, the choice goes to `useDraftShareStore`
 * and the first message carries it. Same drawer, same search and levels as a real chat.
 */
export function createDraftShareAdapter(): ShareAdapter {
  const meId = () => useUserStore.getState().profile?.userId;

  return {
    entityType: 'conversation',
    entityId: 'new',
    sidebarTitle: t('chat.collab.share.title'),
    supportsRoles: true,
    supportsTeams: true,
    teamRolesEditable: true,
    roleOptions: [
      { role: 'WRITER', label: t('chat.collab.share.roleWrite'), description: t('chat.collab.share.roleWriteDescription') },
      { role: 'READER', label: t('chat.collab.share.roleRead'), description: t('chat.collab.share.roleReadDescription') },
    ],
    notice: t('chat.collab.share.draftNotice'),
    noteField: { maxLength: NOTE_MAX_LENGTH },
    maxPerSubmit: COLLABORATORS_PER_PUT_MAX,

    async getSharedMembers(): Promise<SharedMember[]> {
      const profile = useUserStore.getState().profile;
      const owner: SharedMember = {
        id: profile?.userId ?? 'me',
        type: 'user',
        name: profile?.fullName || profile?.email || '',
        role: 'OWNER',
        isOwner: true,
        isCurrentUser: true,
      };
      return [
        owner,
        ...useDraftShareStore.getState().principals.map<SharedMember>((p) => ({
          id: p.id,
          type: p.type,
          name: p.name,
          email: p.email,
          memberCount: p.memberCount,
          role: p.level === 'write' ? 'WRITER' : 'READER',
          isOwner: false,
          isCurrentUser: false,
        })),
      ];
    },

    getMode: (): ShareMode => 'manage',

    async share(submission: ShareSubmission, selections: ShareSelection[] = []): Promise<void> {
      const picked = new Map(selections.map((s) => [`${s.type}:${s.id}`, s]));
      useDraftShareStore.getState().add(
        submission.principals.map((p) => {
          const s = picked.get(`${p.principalType}:${p.principalId}`);
          return {
            type: p.principalType,
            id: p.principalId,
            name: s?.name ?? p.principalId,
            ...(s?.email ? { email: s.email } : {}),
            ...(s?.memberCount !== undefined ? { memberCount: s.memberCount } : {}),
            level: levelOf(p.role),
          };
        }),
        { message: submission.note, confirmOrgWide: submission.confirmOrgWide },
      );
    },

    async updateRole(memberId, memberType, newRole): Promise<void> {
      useDraftShareStore.getState().setLevel(memberType, memberId, levelOf(newRole));
    },

    async removeMember(memberId, memberType): Promise<void> {
      useDraftShareStore.getState().remove(memberType, memberId);
    },

    requiresConfirm: (_submission, selections) => requiresShareConfirm(selections),
    formatError: formatShareError,

    getSharingUsersPaginated: (params) =>
      fetchShareUsersPaginated(params, {
        exclude: (u) => u.isDisabled === true || u.kind === 'service' || u.userId === meId(),
      }),
  };
}
