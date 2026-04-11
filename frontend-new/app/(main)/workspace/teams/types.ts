// ========================================
// Team entity
// ========================================

export type TeamMemberRole = 'OWNER' | 'READER' | 'WRITER';

export interface Team {
  /** UUID primary key */
  id: string;
  name: string;
  description: string | null;
  /** UUID of the user who created the team */
  createdBy: string;
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
  /** User UUID (not MongoDB ObjectId) */
  userId: string;
  role: TeamMemberRole;
}

export interface CreateTeamPayload {
  name: string;
  description?: string;
  userRoles?: CreateTeamUserRole[];
}

export interface UpdateTeamPayload {
  name?: string;
  description?: string;
  updateUserRoles?: CreateTeamUserRole[];
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
