// ========================================
// Group entity (matches GET /api/v1/userGroups response)
// ========================================

export type GroupType = 'admin' | 'everyone' | 'standard' | 'custom';

export interface Group {
  /** MongoDB ObjectId */
  _id: string;
  name: string;
  /** Group type: admin, everyone, standard, custom */
  type: GroupType | string;
  orgId: string;
  /** Array of MongoDB user IDs (matches User.userId) */
  users: string[];
  isDeleted: boolean;
  /** ISO date string */
  createdAt: string;
  /** ISO date string */
  updatedAt: string;
  slug: string;
  __v?: number;
}

// ========================================
// API response shapes
// ========================================

/** Response from GET /api/v1/userGroups */
export interface GroupsListResponse {
  groups: Group[];
  /** Map of userId → data URI (e.g. "data:image/jpeg;base64,...") */
  userDps: Record<string, string>;
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
