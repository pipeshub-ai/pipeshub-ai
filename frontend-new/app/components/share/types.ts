// ============================================================================
// SHARE COMPONENT TYPES (entity-agnostic)
// ============================================================================

/** Supported entity types for sharing */
export type ShareEntityType = 'collection' | 'conversation' | 'search';

/** Permission roles */
export type ShareRole = 'OWNER' | 'WRITER' | 'READER';

/** Display labels for roles */
export const SHARE_ROLE_LABELS: Record<ShareRole, { label: string; description: string }> = {
  OWNER: { label: 'Full Access', description: 'Shared ownership of the collection' },
  WRITER: { label: 'Can edit', description: 'Only edit & organise files & folders' },
  READER: { label: 'Can view', description: 'Only viewing access' },
};

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
  /**
   * The identifier expected by the current entity's share endpoint.
   * For collections/KB this is the graph UUID; for chat this is the MongoDB ObjectID.
   */
  id: string;
  /**
   * The graph UUID for this user. Always set when available and used by flows that
   * operate on UUIDs regardless of which adapter produced the list (e.g. team creation,
   * which requires UUIDs even when the surrounding entity uses MongoDB IDs).
   */
  uuid?: string;
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
export interface ShareAdapter {
  entityType: ShareEntityType;
  entityId: string;
  sidebarTitle: string;

  supportsRoles: boolean;
  supportsTeams: boolean;

  getSharedMembers: () => Promise<SharedMember[]>;
  share: (submission: ShareSubmission) => Promise<void>;
  updateRole?: (memberId: string, memberType: 'user' | 'team', newRole: ShareRole) => Promise<void>;
  removeMember: (memberId: string, memberType: 'user' | 'team') => Promise<void>;

  /**
   * Override the user list shown in the share sidebar search/suggestions.
   * When provided, this replaces ShareCommonApi.getAllUsers().
   * Use when the entity's share endpoint requires a different ID type
   * (e.g. chat uses MongoDB ObjectIDs, collections use UUIDs).
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
