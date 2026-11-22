/**
 * Group repository: named sets of people inside one organisation. Every
 * organisation has an "everyone" group that new accounts join.
 */

export type GroupType = 'admin' | 'standard' | 'custom' | 'everyone';

export const GROUP_TYPES: readonly GroupType[] = [
  'admin',
  'standard',
  'everyone',
  'custom',
];

export interface GroupDto {
  _id: string;
  slug?: string;
  name: string;
  type: GroupType;
  orgId: string;
  users: string[];
  isDeleted?: boolean;
  deletedBy?: string;
  createdAt?: Date;
  updatedAt?: Date;
  __v?: number;
}

export interface GroupListQuery {
  skip: number;
  limit: number;
  search?: string;
  createdAfter?: string;
  createdBefore?: string;
}

/** A group a person belongs to, as lists show it. */
export interface GroupSummaryDto {
  _id: string;
  name: string;
  type: string;
}

export interface GroupStat {
  _id: string;
  count: number;
  totalUsers: number;
  avgUsers: number;
}

export interface GroupRepository {
  list(
    orgId: string,
    q: GroupListQuery,
  ): Promise<{ rows: GroupDto[]; total: number }>;
  /** The group whether or not it was deleted. */
  get(orgId: string, id: string): Promise<GroupDto | null>;
  getLive(orgId: string, id: string): Promise<GroupDto | null>;
  /** A live group with this name; `exceptId` leaves one group out (a rename). */
  nameTaken(orgId: string, name: string, exceptId?: string): Promise<boolean>;
  /** Throws `UniqueConstraintError{key:'name'}` when a live group has the name. */
  create(
    orgId: string,
    input: { name: string; type: GroupType },
  ): Promise<GroupDto>;
  /** Same error as `create`; null when there is no live group. */
  rename(orgId: string, id: string, name: string): Promise<GroupDto | null>;
  softDelete(
    orgId: string,
    id: string,
    deletedBy?: string,
  ): Promise<GroupDto | null>;

  /** Adds these people to the live groups among `groupIds`; returns how many groups changed. */
  addMembers(
    orgId: string,
    groupIds: string[],
    userIds: string[],
  ): Promise<number>;
  removeMembers(
    orgId: string,
    groupIds: string[],
    userIds: string[],
  ): Promise<number>;
  /** Adds one person to these groups, deleted or not (invites). */
  addMemberToGroups(
    orgId: string,
    groupIds: string[],
    userId: string,
  ): Promise<void>;
  /** With `liveOnly`, a removed "everyone" group is left alone (the single-sign-on and admin-created account paths). */
  addToEveryone(
    orgId: string,
    userId: string,
    opts?: { liveOnly?: boolean },
  ): Promise<void>;
  removeFromEveryone(orgId: string, userId: string): Promise<void>;
  /** Takes the user out of the groups that hold them; with `everyGroup`, every group of the organisation is written. */
  removeFromAll(
    orgId: string,
    userId: string,
    opts?: { everyGroup?: boolean },
  ): Promise<void>;

  groupsOfUser(orgId: string, userId: string): Promise<GroupSummaryDto[]>;
  /** Member ids (as stored, repeats possible) of the live groups of these types. */
  memberIdsOfTypes(orgId: string, types: string[]): Promise<string[]>;
  /** Live groups containing any of these people, with their member lists. */
  summariesForUsers(
    orgId: string,
    userIds: string[],
  ): Promise<Array<GroupSummaryDto & { users: string[] }>>;
  stats(orgId: string): Promise<GroupStat[]>;
}
