// ========================================
// Team entity
// ========================================

export type TeamMemberRole = 'OWNER' | 'READER' | 'WRITER';

export interface TeamCreatedByUser {
  /** Graph user key of the team creator */
  id: string;
  userId: string;
  name: string;
  email: string;
  profilePicture?: string | null;
}

export interface Team {
  /** UUID primary key */
  id: string;
  name: string;
  description: string | null;
  createdByUser?: TeamCreatedByUser | null;
  orgId: string;
  createdAtTimestamp: number;
  updatedAtTimestamp: number;

  /** Current user's permission on this team */
  currentUserPermission?: TeamPermission;

  /** Array of team members */
  members: TeamMember[];
  /** Total member count */
  memberCount: number;

  /** Permission flags for the current user */
  canEdit: boolean;
  canDelete: boolean;
  canManageMembers: boolean;
}

export interface TeamMember {
  /** User UUID */
  id: string;
  /** MongoDB user ID */
  userId: string;
  userName: string;
  userEmail: string;
  role: TeamMemberRole | string;
  joinedAt: number;
  isOwner: boolean;
  /** Data URI for profile picture, if available */
  profilePicture?: string;
}

export interface TeamPermission {
  _key: string;
  _id: string;
  _from: string;
  _to: string;
  _rev: string;
  type: string;
  role: string;
  createdAtTimestamp: number;
  updatedAtTimestamp: number;
}

// ========================================
// API request shapes
// ========================================

export interface CreateTeamUserRole {
  /** MongoDB ObjectId (not graph UUID) */
  userId: string;
  role: TeamMemberRole;
}

export interface CreateTeamPayload {
  name: string;
  description?: string;
  userRoles?: CreateTeamUserRole[];
}

/** MongoDB ObjectId — used in addUserRoles when updating a team */
export interface TeamAddMemberRole {
  userId: string;
  role: TeamMemberRole;
}

/** MongoDB ObjectId — used in updateUserRoles when updating a team */
export interface TeamMemberRoleUpdate {
  userId: string;
  role: TeamMemberRole;
}

export interface UpdateTeamPayload {
  name?: string;
  description?: string;
  addUserRoles?: TeamAddMemberRole[];
  /** MongoDB ObjectId of members to remove */
  removeUserIds?: string[];
  updateUserRoles?: TeamMemberRoleUpdate[];
}

// ========================================
// API response shapes
// ========================================

export interface TeamsPagination {
  page: number;
  limit: number;
  total: number;
  pages: number;
  hasNext: boolean;
  hasPrev: boolean;
}

/** Response from GET /api/v1/teams/user/teams */
export interface TeamsListResponse {
  status: string;
  message: string;
  teams: Team[];
  pagination: TeamsPagination;
}

// ========================================
// Filters
// ========================================

import type { DateFilterType } from '@/app/components/ui/date-range-picker';

export interface TeamsFilter {
  createdBy?: string[];
  createdAfter?: string;
  createdBefore?: string;
  createdDateType?: DateFilterType;
}

// ========================================
// Sort
// ========================================

export type TeamSortField = 'name' | 'memberCount' | 'createdAtTimestamp';

export interface TeamsSort {
  field: TeamSortField;
  order: 'asc' | 'desc';
}
