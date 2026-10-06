import i18next from 'i18next';
import { fetchShareUsersPaginated } from '@/app/components/share/utils';
import type {
  ShareAdapter,
  ShareMode,
  ShareConfirmSpec,
  ShareRole,
  ShareSelection,
  SharedMember,
  ShareSubmission,
} from '@/app/components/share/types';
import { useUserStore } from '@/lib/store/user-store';
import {
  CollaborationApi,
  conversationErrorDetails,
  isConversationError,
} from './collaboration-api';
import {
  isCollaboratorsView,
  type CollaboratorAccessLevel,
  type CollaboratorsResponse,
  type CollaboratorsSummary,
  type CollaboratorsView,
  type ConversationRef,
} from './collaboration-types';
import { conversationErrorMessage } from './utils/conversation-errors';

export const NOTE_MAX_LENGTH = 500;
/** Mirrors the backend's per-PUT limit on collaborators. */
export const COLLABORATORS_PER_PUT_MAX = 50;
const LARGE_TEAM_MEMBER_COUNT = 50;
const ORG_WIDE_PREFIX = 'all_';

const t = (key: string, options?: Record<string, unknown>) => i18next.t(key, options) as string;

function toAccessLevel(role: ShareRole): CollaboratorAccessLevel {
  return role === 'WRITER' ? 'write' : 'read';
}

function levelLabel(level: string): string {
  return level === 'write' || level === 'editor' || level === 'owner'
    ? t('chat.collab.share.roleWrite')
    : t('chat.collab.share.roleRead');
}

export function formatShareError(error: unknown): string {
  const details = conversationErrorDetails(error);
  if (isConversationError(error, 'RATE_LIMITED') && typeof details?.retryAfter === 'number') {
    return t('chat.collab.share.rateLimited', { retryAfter: details.retryAfter });
  }
  if (isConversationError(error, 'COLLABORATOR_LIMIT') && typeof details?.max === 'number') {
    return t('chat.collab.share.limitReached', { max: details.max });
  }
  return conversationErrorMessage(i18next.t.bind(i18next), error);
}

/** Org-wide and large-team grants ask first; the same rule applies when the grant is only drafted. */
export function requiresShareConfirm(selections: ShareSelection[]): ShareConfirmSpec | null {
  const teams = selections.filter((s) => s.type === 'team');
  if (teams.some((s) => s.id.startsWith(ORG_WIDE_PREFIX))) {
    return {
      title: t('chat.collab.share.orgWideTitle'),
      message: t('chat.collab.share.orgWideMessage'),
      confirmLabel: t('chat.collab.share.orgWideConfirm'),
      orgWide: true,
    };
  }
  const large = teams.find((s) => (s.memberCount ?? 0) > LARGE_TEAM_MEMBER_COUNT);
  if (large) {
    return {
      title: t('chat.collab.share.largeTeamTitle'),
      message: t('chat.collab.share.largeTeamMessage', { name: large.name, members: large.memberCount }),
      confirmLabel: t('chat.collab.share.largeTeamConfirm'),
    };
  }
  return null;
}

/**
 * ShareAdapter for a chat or an agent chat over `CollaborationApi` (PUT/DELETE collaborators,
 * settings, transfer). Used only with `ENABLE_COLLABORATIVE_CHATS` on; the legacy adapter in
 * `share-adapter.ts` serves the flag-off path.
 */
export function createCollabChatShareAdapter(
  conversationId: string,
  options?: { agentId?: string },
): ShareAdapter {
  const ref: ConversationRef = options?.agentId
    ? { kind: 'agent', agentKey: options.agentId, id: conversationId }
    : { kind: 'chat', id: conversationId };

  let last: CollaboratorsResponse | null = null;

  const currentUserId = () => useUserStore.getState().profile?.userId;
  const remember = (next: CollaboratorsResponse) => {
    last = next;
    return next;
  };
  const view = (): CollaboratorsView | null => (last && isCollaboratorsView(last) ? last : null);
  const summary = (): CollaboratorsSummary | null => (last && !isCollaboratorsView(last) ? last : null);

  const isOwner = () => {
    const v = view();
    const me = currentUserId();
    return v !== null && !!me && v.owner.userId === me;
  };

  return {
    entityType: 'conversation',
    entityId: conversationId,
    sidebarTitle: t('chat.collab.share.title'),
    supportsRoles: true,
    supportsTeams: true,
    teamRolesEditable: true,
    roleOptions: [
      { role: 'WRITER', label: t('chat.collab.share.roleWrite'), description: t('chat.collab.share.roleWriteDescription') },
      { role: 'READER', label: t('chat.collab.share.roleRead'), description: t('chat.collab.share.roleReadDescription') },
    ],
    notice: t('chat.collab.share.notice'),
    noteField: { maxLength: NOTE_MAX_LENGTH },
    maxPerSubmit: COLLABORATORS_PER_PUT_MAX,

    async getSharedMembers(): Promise<SharedMember[]> {
      const response = remember(await CollaborationApi.getCollaborators(ref));
      if (!isCollaboratorsView(response)) return [];
      const me = currentUserId();
      const members: SharedMember[] = [
        {
          id: response.owner.userId,
          type: 'user',
          name: response.owner.displayName,
          role: 'OWNER',
          isOwner: true,
          isCurrentUser: response.owner.userId === me,
        },
      ];
      for (const c of response.collaborators) {
        const state = c.state === 'active' ? undefined : c.state;
        members.push({
          id: c.principalId,
          type: c.principalType,
          name:
            state === 'former_member'
              ? t('chat.collab.share.formerMember')
              : state === 'deleted_team'
                ? t('chat.collab.share.deletedTeam')
                : c.displayName,
          role: c.accessLevel === 'write' ? 'WRITER' : 'READER',
          isOwner: false,
          isCurrentUser: c.principalType === 'user' && c.principalId === me,
          state,
        });
      }
      return members;
    },

    getMode(): ShareMode {
      if (summary()) return 'summary';
      return isOwner() ? 'manage' : 'invite';
    },

    getAccessSummary() {
      const s = summary();
      return s
        ? { count: s.collaboratorCount, ownerName: s.owner.displayName, myAccessLabel: levelLabel(s.myAccess) }
        : null;
    },

    async share(submission: ShareSubmission): Promise<void> {
      const hasOrgWide = submission.principals.some(
        (p) => p.principalType === 'team' && p.principalId.startsWith(ORG_WIDE_PREFIX),
      );
      remember(
        await CollaborationApi.putCollaborators(ref, {
          collaborators: submission.principals.map((p) => ({
            principalType: p.principalType,
            principalId: p.principalId,
            accessLevel: toAccessLevel(p.role),
          })),
          note: submission.note,
          confirmOrgWide: submission.confirmOrgWide && hasOrgWide ? true : undefined,
        }),
      );
    },

    async updateRole(memberId, memberType, newRole): Promise<void> {
      remember(
        await CollaborationApi.putCollaborators(ref, {
          collaborators: [{ principalType: memberType, principalId: memberId, accessLevel: toAccessLevel(newRole) }],
        }),
      );
    },

    async removeMember(memberId, memberType): Promise<void> {
      remember(await CollaborationApi.removeCollaborator(ref, memberId, memberType));
    },

    async transferOwnership(memberId: string): Promise<void> {
      remember(await CollaborationApi.transferOwnership(ref, memberId));
    },

    async settings() {
      const v = view();
      if (!v) return [];
      return [
        {
          id: 'editorsCanInvite',
          label: t('chat.collab.share.settingEditorsCanInvite'),
          description: t('chat.collab.share.settingEditorsCanInviteDescription'),
          value: v.settings.editorsCanInvite,
        },
        {
          id: 'ownerContentShared',
          label: t('chat.collab.share.settingOwnerContentShared'),
          description: t('chat.collab.share.settingOwnerContentSharedDescription'),
          value: v.settings.ownerContentShared,
        },
      ];
    },

    async updateSetting(id, value): Promise<void> {
      if (id !== 'editorsCanInvite' && id !== 'ownerContentShared') return;
      remember(await CollaborationApi.patchSettings(ref, { [id]: value }));
    },

    requiresConfirm: (_submission, selections) => requiresShareConfirm(selections),

    formatError: formatShareError,

    /** The server refuses disabled users, service accounts and the owner, so they are not suggested. */
    getSharingUsersPaginated: (params) => {
      const ownerId = view()?.owner.userId;
      return fetchShareUsersPaginated(params, {
        exclude: (u) => u.isDisabled === true || u.kind === 'service' || u.userId === ownerId,
      });
    },
  };
}
