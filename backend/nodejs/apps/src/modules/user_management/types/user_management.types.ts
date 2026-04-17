import { FilterQuery } from 'mongoose';

// ── Graph API response types (from Python backend) ──

export interface GraphUser {
  id: string;
  userId: string;
  orgId: string;
  name?: string;
  email?: string;
  isActive?: boolean;
  createdAtTimestamp?: number;
  updatedAtTimestamp?: number;
  // Enriched by Node.js backend
  profilePicture?: string;
  hasLoggedIn?: boolean;
  role?: string;
  groupCount?: number;
  userGroups?: UserGroupSummary[];
}

export interface GraphUserListResponse {
  users: GraphUser[];
  pagination?: {
    page: number;
    limit: number;
    total: number;
    totalPages?: number;
  };
}

export interface UserGroupSummary {
  _id: string;
  name: string;
  type: string;
}

// ── Team types (from Python backend) ──

export interface TeamMemberResponse {
  id: string;
  userId: string;
  userName: string;
  userEmail: string;
  role: string;
  joinedAt?: number;
  isOwner: boolean;
  profilePicture?: string;
}

export interface TeamsListResponse {
  teams: Array<{
    id: string;
    name: string;
    description?: string | null;
    createdBy: string;
    orgId: string;
    memberCount: number;
    members?: TeamMemberResponse[];
    [key: string]: unknown;
  }>;
  pagination: {
    page: number;
    limit: number;
    total: number;
    pages: number;
    hasNext: boolean;
    hasPrev: boolean;
  };
}

export interface TeamUsersResponse {
  members: TeamMemberResponse[];
}

// ── MongoDB filter types ──

export type UserGroupFilter = FilterQuery<{
  orgId: string;
  isDeleted: boolean;
  name?: { $regex: string; $options: string };
}>;

export type UserFilter = FilterQuery<{
  _id: { $in: string[] };
  isDeleted: { $ne: boolean };
  $or?: Array<{ fullName?: { $regex: string; $options: string }; email?: { $regex: string; $options: string } }>;
}>;
