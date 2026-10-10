'use client';

import type { TFunction } from 'i18next';

// ============================================================================
// SHARE COMPONENT TYPES (entity-agnostic)
// ============================================================================

/** Supported entity types for sharing */
export type ShareEntityType = 'collection' | 'conversation' | 'search' | 'connector' | 'agent' | 'project';

/** Permission roles */
export type ShareRole = 'OWNER' | 'WRITER' | 'COMMENTER' | 'READER';

/** Roles a team grant may carry. A team grant covers every current and future member, so never OWNER. */
export const TEAM_SHARE_ROLES: readonly ShareRole[] = ['WRITER', 'COMMENTER', 'READER'];

export const DEFAULT_TEAM_SHARE_ROLE: ShareRole = 'READER';

/** Clamp any role to one a team may hold (never OWNER). */
export function toTeamShareRole(role: ShareRole | undefined): ShareRole {
  return role && TEAM_SHARE_ROLES.includes(role) ? role : DEFAULT_TEAM_SHARE_ROLE;
}

/** Localized display labels for roles. */
export function getShareRoleLabels(t: TFunction): Record<ShareRole, { label: string; description: string }> {
  return {
    OWNER: { label: t('shareSidebar.roles.owner.label'), description: t('shareSidebar.roles.owner.description') },
    WRITER: { label: t('shareSidebar.roles.writer.label'), description: t('shareSidebar.roles.writer.description') },
    COMMENTER: { label: t('shareSidebar.roles.commenter.label'), description: t('shareSidebar.roles.commenter.description') },
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
  /** A person who left the organization or a team that was deleted: only removal makes sense. */
  state?: 'former_member' | 'deleted_team';
}

/** An item selected in the search input (before submission) */
export interface ShareSelection {
  type: 'team' | 'user';
  id: string;
  name: string;
  email?: string;
  memberCount?: number;
  /** Role for a selected team (teams carry their own role; users share the sidebar role). */
  role?: ShareRole;
  /** True when the email was typed manually but no matching org user was found */
  isInvalid?: boolean;
}

/** What gets submitted when the user clicks "Share" */
export interface ShareSubmission {
  userIds: string[];
  teamIds: string[];
  /** Role for users; teams carry their own in `principals`. */
  role: ShareRole;
  /** One entry per grantee with its own role. */
  principals: ShareSubmissionPrincipal[];
  /** Optional hand-over note, set by the sidebar when the adapter has a `noteField`. */
  note?: string;
  /** Set by the sidebar once the user confirmed a spec with `orgWide`. */
  confirmOrgWide?: true;
}

export interface ShareSubmissionPrincipal {
  principalType: 'user' | 'team';
  principalId: string;
  role: ShareRole;
}

/** Build the submission: users get `userRole`, each team its own role (clamped, never OWNER). */
export function buildShareSubmission(
  selections: ShareSelection[],
  userRole: ShareRole,
): ShareSubmission {
  const users = selections.filter((s) => s.type === 'user');
  const teams = selections.filter((s) => s.type === 'team');
  return {
    userIds: users.map((s) => s.id),
    teamIds: teams.map((s) => s.id),
    role: userRole,
    principals: [
      ...users.map((s) => ({ principalType: 'user' as const, principalId: s.id, role: userRole })),
      ...teams.map((s) => ({ principalType: 'team' as const, principalId: s.id, role: toTeamShareRole(s.role) })),
    ],
  };
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

/** A level the adapter offers when sharing or changing a row, with its own wording. */
export interface ShareRoleOption {
  role: ShareRole;
  label: string;
  description: string;
}

/** A boolean setting rendered as a switch row under the members list. */
export interface ShareSettingDescriptor {
  id: string;
  label: string;
  description?: string;
  value: boolean;
}

/** A confirmation the user must accept before the share goes out. */
export interface ShareConfirmSpec {
  title: string;
  message: string;
  confirmLabel: string;
  /** Sidebar then sends `confirmOrgWide: true` with the submission. */
  orgWide?: boolean;
}

/**
 * What the caller may do, known once members are loaded.
 * `manage`: add, change, remove. `invite`: add only. `summary`: a count and the caller's own access.
 */
export type ShareMode = 'manage' | 'invite' | 'summary';

export interface ShareAccessSummary {
  count: number;
  ownerName: string;
  /** Already localized, e.g. "Can continue". */
  myAccessLabel: string;
}

/** The adapter interface — each entity page implements this */
export interface ShareAdapter {
  entityType: ShareEntityType;
  entityId: string;
  sidebarTitle: string;

  supportsRoles: boolean;
  supportsTeams: boolean;
  /** Existing team rows carry a role the owner can change (edit / comment / view). */
  teamRolesEditable?: boolean;

  getSharedMembers: () => Promise<SharedMember[]>;
  /** `selections` carries the names and emails of what was picked, for adapters that only store the choice. */
  share: (submission: ShareSubmission, selections?: ShareSelection[]) => Promise<void>;
  updateRole?: (memberId: string, memberType: 'user' | 'team', newRole: ShareRole) => Promise<void>;
  removeMember: (memberId: string, memberType: 'user' | 'team') => Promise<void>;

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

  /** Levels offered for new shares and existing rows (replaces the default role list). */
  roleOptions?: ShareRoleOption[];
  /** Switch rows shown to the owner. Read after `getSharedMembers`. */
  settings?: () => Promise<ShareSettingDescriptor[]>;
  updateSetting?: (id: string, value: boolean) => Promise<void>;
  /** Disclosure shown above the search input. */
  notice?: string;
  /** Most people and teams one `share` call accepts; the sidebar blocks Share above it. */
  maxPerSubmit?: number;
  /** Shows an optional note box while people are selected. */
  noteField?: { maxLength: number };
  /** Adds "Make owner" to rows of people who may become owner. */
  transferOwnership?: (memberId: string) => Promise<void>;
  /** Return a spec to make the user confirm before `share` is called. */
  requiresConfirm?: (submission: ShareSubmission, selections: ShareSelection[]) => ShareConfirmSpec | null;
  /** What the caller can do. Read after `getSharedMembers`; absent means `manage`. */
  getMode?: () => ShareMode;
  /** Read after `getSharedMembers` when the mode is `summary`. */
  getAccessSummary?: () => ShareAccessSummary | null;
  /** When present, failures are shown inline with this text instead of as a generic toast. */
  formatError?: (error: unknown) => string;
}
