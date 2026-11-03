'use client';

import type { TFunction } from 'i18next';

// ============================================================================
// SHARE COMPONENT TYPES (entity-agnostic)
// ============================================================================

/** Supported entity types for sharing */
export type ShareEntityType = 'collection' | 'conversation' | 'search' | 'connector' | 'agent' | 'project' | 'artifact';

/** Permission roles */
export type ShareRole = 'OWNER' | 'WRITER' | 'READER';

/** Localized display labels for roles. */
export function getShareRoleLabels(t: TFunction): Record<ShareRole, { label: string; description: string }> {
  return {
    OWNER: { label: t('shareSidebar.roles.owner.label'), description: t('shareSidebar.roles.owner.description') },
    WRITER: { label: t('shareSidebar.roles.writer.label'), description: t('shareSidebar.roles.writer.description') },
    READER: { label: t('shareSidebar.roles.reader.label'), description: t('shareSidebar.roles.reader.description') },
  };
}

/** A member with whom the entity is already shared */
export interface SharedMember {
  id: string;
  type: 'user' | 'team';
  name: string;
  email?: string;
  avatarUrl?: string;
  memberCount?: number;
  role: ShareRole;
  isOwner: boolean;
  isCurrentUser: boolean;
}

/** An item selected in the search input (before submission) */
export interface ShareSelection {
  type: 'team' | 'user';
  id: string;
  name: string;
  email?: string;
  memberCount?: number;
  /** True when the email was typed manually but no matching org user was found */
  isInvalid?: boolean;
}

/** What gets submitted when the user clicks "Share" */
export interface ShareSubmission {
  userIds: string[];
  teamIds: string[];
  role: ShareRole;
}

/** Team entity */
export interface ShareTeam {
  id: string;
  name: string;
  description?: string;
  memberCount: number;
}

/** Org user (for suggestions) */
export interface ShareUser {
  /** MongoDB userId used by share endpoints (KB backend resolves to graph keys). */
  id: string;
  name: string;
  email?: string;
  avatarUrl?: string;
  isInOrg: boolean;
}

/** For the SharedAvatarStack component */
export interface SharedAvatarMember {
  id: string;
  name: string;
  avatarUrl?: string;
  type?: 'user' | 'team';
}

/** The adapter interface — each entity page implements this */
/** Thrown by an adapter that has already shown why an action failed, so the sidebar adds no toast. */
export class ShareFeedbackShownError extends Error {}

export interface ShareAdapter {
  entityType: ShareEntityType;
  entityId: string;
  sidebarTitle: string;

  supportsRoles: boolean;
  supportsTeams: boolean;
  /** Teams carry a role like users do (default: teams have no role). */
  teamsHaveRoles?: boolean;
  /** Ask before removing someone's access (default: remove straight away). */
  confirmBeforeRemove?: boolean;
  /** Narrow the role picker to some roles and/or reword them (default: all three, house labels). */
  roleOptions?: {
    roles: ShareRole[];
    labels?: Record<ShareRole, { label: string; description: string }>;
  };

  getSharedMembers: () => Promise<SharedMember[]>;
  share: (submission: ShareSubmission) => Promise<void>;
  updateRole?: (memberId: string, memberType: 'user' | 'team', newRole: ShareRole) => Promise<void>;
  /** Resolve `{ removed: false }` when the member had already lost access, so the sidebar doesn't claim it removed them. */
  removeMember: (memberId: string, memberType: 'user' | 'team') => Promise<void | { removed: boolean }>;

  /**
   * Override the user list shown in the share sidebar search/suggestions.
   * When provided, this replaces ShareCommonApi.getAllUsers().
   * Use when the entity needs a custom user list instead of the default Mongo list.
   */
  getSharingUsers?: () => Promise<ShareUser[]>;

  /**
   * Paginated user fetcher for the share sidebar search/suggestions.
   * When provided, enables infinite scroll instead of loading all users at once.
   * Takes priority over getSharingUsers.
   */
  getSharingUsersPaginated?: (params: {
    page: number;
    limit: number;
    search?: string;
  }) => Promise<{ users: ShareUser[]; totalCount: number }>;
}
