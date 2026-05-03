// ========================================
// Group entity (matches GET /api/v1/userGroups response)
// ========================================

export type GroupType = 'admin' | 'everyone' | 'standard' | 'custom';

export function isSystemGroup(group: Pick<Group, 'type'>): boolean {
  return group.type !== 'custom';
}

export interface Group {
  /** MongoDB ObjectId */
  _id: string;
  name: string;
  /** Group type: admin, everyone, standard, custom */
  type: GroupType | string;
  orgId: string;
  /** Number of users in this group */
  userCount: number;
  isDeleted: boolean;
  /** ISO date string */
  createdAt: string;
  /** ISO date string */
  updatedAt: string;
  slug: string;
}

/** A user within a group (returned by GET /userGroups/:groupId/users) */
export interface GroupUser {
  _id: string;
  fullName: string | null;
  email: string | null;
  profilePicture: string | null;
}

// ========================================
// API response shapes
// ========================================

export interface GroupsPagination {
  page: number;
  limit: number;
  totalCount: number;
  totalPages: number;
  hasNextPage: boolean;
  hasPrevPage: boolean;
}

/** Response from GET /api/v1/userGroups */
export interface GroupsListResponse {
  groups: Group[];
  pagination: GroupsPagination;
}

/** Response from GET /api/v1/userGroups/:groupId/users */
export interface GroupUsersResponse {
  users: GroupUser[];
  pagination: GroupsPagination;
}

// ========================================
// Filters
// ========================================

import type { DateFilterType } from '@/app/components/ui/date-range-picker';

export interface GroupsFilter {
  type?: GroupType[];
  createdAfter?: string;
  createdBefore?: string;
  createdDateType?: DateFilterType;
}

// ========================================
// Sort
// ========================================

export type GroupSortField = 'name' | 'type' | 'createdAt';

export interface GroupsSort {
  field: GroupSortField;
  order: 'asc' | 'desc';
}
