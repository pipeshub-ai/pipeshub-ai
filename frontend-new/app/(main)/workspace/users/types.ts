// ========================================
// User entity (merged from graph/list + with-groups APIs)
// ========================================

export interface User {
  /** UUID identifier (from graph API, or MongoDB _id as fallback) */
  id: string;
  /** MongoDB ObjectID */
  userId: string;
  /** Display name (may be absent for invited users) */
  name?: string;
  /** Email address (from graph API, may be absent) */
  email?: string;
  /** Whether the user has ever logged in (from with-groups API) */
  hasLoggedIn: boolean;
  /** Whether the user is currently active */
  isActive: boolean;
  /** Unix timestamp in milliseconds (absent for pending users) */
  createdAtTimestamp?: number;
  /** Unix timestamp in milliseconds (absent for pending users) */
  updatedAtTimestamp?: number;

  // ── Derived fields (computed during API merge) ──

  /** Role derived from group membership: "Admin" or "Member" */
  role?: string;
  /** Number of groups the user belongs to (excluding "everyone") */
  groupCount?: number;
  /** Data URI for profile picture, if available */
  profilePicture?: string;
  /** Inline groups from with-groups API */
  userGroups?: Array<{ _id?: string; name: string; type: string }>;

  /** Account blocked (credentials); from GET /api/v1/users?blocked=true merge */
  isBlocked?: boolean;
}

/**
 * One document from POST /api/v1/users/by-ids (raw `users` collection / Mongoose JSON).
 * Differs from {@link User}: mongo `_id` and `fullName` instead of `userId` / `name`.
 */
export interface UserByIdsDoc {
  _id: string | { toString(): string };
  fullName?: string;
  email?: string;
  hasLoggedIn?: boolean;
}

/** Single row from GET /api/v1/users?blocked=true */
export interface BlockedUserRecord {
  _id: string;
  email: string;
  fullName?: string;
  hasLoggedIn?: boolean;
  orgId?: string;
  createdAt?: string;
  updatedAt?: string;
}

// ========================================
// With-Groups API response
// ========================================

/** Single user from GET /api/v1/users/fetch/with-groups */
export interface WithGroupsUser {
  _id: string;
  orgId: string;
  fullName?: string;
  hasLoggedIn: boolean;
  groups: Array<{ name: string; type: string }>;
}

// ========================================
// API response shapes
// ========================================

/** Pagination from MongoDB-backed endpoints (GET /api/v1/users) */
export interface UsersPagination {
  page: number;
  limit: number;
  totalCount: number;
  totalPages: number;
  hasNextPage: boolean;
  hasPrevPage: boolean;
}

/** Pagination from graph-backed endpoints (GET /api/v1/users/graph/list) */
export interface GraphPagination {
  page: number;
  limit: number;
  total: number;
  pages: number;
  hasNext: boolean;
  hasPrev: boolean;
}

/** Response from GET /api/v1/users (MongoDB) */
export interface UsersListResponse {
  status: string;
  message: string;
  users: User[];
  pagination: UsersPagination;
}

/** Response from GET /api/v1/users/graph/list */
export interface GraphUsersListResponse {
  status: string;
  message: string;
  users: User[];
  pagination: GraphPagination;
}

// ========================================
// Filters
// ========================================

import type { DateFilterType } from '@/app/components/ui/date-range-picker';

export interface UsersFilter {
  roles?: string[];
  groups?: string[];
  statuses?: ('Active' | 'Pending' | 'Blocked')[];
  lastActiveAfter?: string;
  lastActiveBefore?: string;
  lastActiveDateType?: DateFilterType;
  dateJoinedAfter?: string;
  dateJoinedBefore?: string;
  dateJoinedDateType?: DateFilterType;
}

// ========================================
// Sort
// ========================================

export type UserSortField = 'name' | 'email' | 'isActive' | 'createdAtTimestamp';

export interface UsersSort {
  field: UserSortField;
  order: 'asc' | 'desc';
}
