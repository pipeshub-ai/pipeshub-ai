import { Response } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import {
  BadRequestError,
  ForbiddenError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { injectable } from 'inversify';
import {
  GROUP_TYPES,
  getGroupRepository,
  type GroupType,
} from '../../../libs/repositories/group';
import { getUserRepository } from '../../../libs/repositories/user';
import { getPictureRepository } from '../../../libs/repositories/picture';
import { UniqueConstraintError } from '../../../libs/repositories/errors';
import { safeParsePagination } from '../../../utils/safe-integer';
import { buildPaginationMetadata } from '../../enterprise_search/utils/utils';
import { isUserOrgAdmin } from '../services/user-admin.service';

const RESERVED_GROUP_NAMES = ['admin', 'everyone', 'standard'];
const GROUP_EXISTS_MESSAGE = 'Group already exists';

// Two requests can both pass the name check; the unique index on
// (orgId, name) then rejects the second write.
async function saveGroupName<T>(write: () => Promise<T>): Promise<T> {
  try {
    return await write();
  } catch (error) {
    if (
      error instanceof UniqueConstraintError &&
      String(error.metadata?.key ?? '').includes('name')
    ) {
      throw new BadRequestError(GROUP_EXISTS_MESSAGE);
    }
    throw error;
  }
}

@injectable()
export class UserGroupController {
  constructor() {}

  // safeParsePagination throws a plain Error for an out-of-range page or limit,
  // which the error middleware reports as a 500. A bad query string is a 400.
  private parsePagination(req: AuthenticatedUserRequest): {
    page: number;
    limit: number;
    skip: number;
  } {
    try {
      return safeParsePagination(
        req.query.page as string,
        req.query.limit as string,
        1,
        25,
        100,
      );
    } catch (error) {
      throw new BadRequestError(
        error instanceof Error
          ? error.message
          : 'Invalid pagination parameters',
      );
    }
  }

  async getAllUsers(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const users = await getUserRepository().listAllLive(
      req.user?.orgId as string,
    );
    res.json(users);
  }

  async createUserGroup(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const { name, type } = req.body;
    if (!name) {
      throw new BadRequestError('name(Name of the Group) is required');
    }

    if (!type) {
      throw new BadRequestError('type(Type of the Group) is required');
    }
    // The route schema already forces strings. Checked again so a query
    // operator ({ "$ne": null }) can never reach the lookup below.
    if (typeof name !== 'string') {
      throw new BadRequestError('name must be a string');
    }
    if (typeof type !== 'string') {
      throw new BadRequestError('type must be a string');
    }
    if (
      RESERVED_GROUP_NAMES.includes(name) ||
      RESERVED_GROUP_NAMES.includes(type)
    ) {
      throw new BadRequestError(
        'Group name or type "admin", "everyone", or "standard" cannot be created',
      );
    }

    if (!GROUP_TYPES.find((groupType) => groupType === type)) {
      throw new BadRequestError('type(Type of the Group) unknown');
    }

    const groups = getGroupRepository();
    if (await groups.nameTaken(req.user?.orgId as string, name)) {
      throw new BadRequestError(GROUP_EXISTS_MESSAGE);
    }

    const group = await saveGroupName(() =>
      groups.create(req.user?.orgId as string, {
        name: name,
        type: type as GroupType,
      }),
    );

    res.status(201).json(group);
  }

  async getAllUserGroups(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const orgId = req.user?.orgId as string;
    const { page, limit, skip } = this.parsePagination(req);
    const search = (req.query.search as string)?.trim();
    const createdAfter = (req.query.createdAfter as string)?.trim();
    const createdBefore = (req.query.createdBefore as string)?.trim();

    const { rows: groups, total: totalCount } = await getGroupRepository().list(
      orgId,
      { skip, limit, search, createdAfter, createdBefore },
    );

    // Return userCount instead of the full users array
    const result = groups.map((group) => {
      const timestamps = group as typeof group & {
        createdAt?: string;
        updatedAt?: string;
      };
      return {
        _id: group._id,
        name: group.name,
        type: group.type,
        orgId: group.orgId,
        slug: group.slug,
        isDeleted: group.isDeleted,
        createdAt: timestamps.createdAt,
        updatedAt: timestamps.updatedAt,
        userCount: group.users?.length ?? 0,
      };
    });

    res.status(200).json({
      groups: result,
      pagination: buildPaginationMetadata(totalCount, page, limit),
    });
  }

  async getUserGroupById(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const groupId = req.params.groupId as string;
    const orgId = req.user?.orgId as string;

    const userGroup = await getGroupRepository().get(orgId, groupId);

    if (!userGroup) {
      throw new NotFoundError('UserGroup not found');
    }

    // `users` is a list of Mongo ids, and a service account's is the value an
    // agent stores when it is bound to that account. Every service account is
    // put in the everyone group when it is created, so without this the group
    // every member can read hands out all of them. The ids of colleagues stay,
    // because they are already the member's to see.
    if (Array.isArray(userGroup.users)) {
      userGroup.users = await this.visibleMembers(req, orgId, userGroup.users);
    }
    res.json(userGroup);
  }

  /**
   * ``ids`` without the service accounts among them, unless the caller
   * administers the organisation. The lookup only runs when one is actually in
   * the list, so an ordinary group costs nothing.
   */
  private async visibleMembers(
    req: AuthenticatedUserRequest,
    orgId: string,
    ids: readonly string[],
  ): Promise<string[]> {
    const members = [...ids];
    if (members.length === 0) return members;

    const accounts = await getUserRepository().serviceAccountIdsAmong(
      orgId,
      members,
    );
    if (accounts.length === 0) return members;

    const isAdmin =
      req.user?.userId !== undefined &&
      (await isUserOrgAdmin(String(req.user.userId), orgId));
    if (isAdmin) return members;

    const hidden = new Set(accounts);
    return members.filter((id) => !hidden.has(id));
  }

  async updateGroup(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const groupId = req.params.groupId as string;
    const { name } = req.body as { name?: string };
    const orgId = req.user?.orgId as string;

    if (!name) {
      throw new BadRequestError('New name is required');
    }

    const normalizedName = name.trim();

    if (!normalizedName) {
      throw new BadRequestError('New name is required');
    }

    const groups = getGroupRepository();
    const group = await groups.getLive(orgId, groupId);

    if (!group) {
      throw new NotFoundError('User group not found');
    }

    if (group.type == 'admin' || group.type == 'everyone') {
      throw new ForbiddenError('Not Allowed');
    }

    if (normalizedName !== group.name) {
      if (RESERVED_GROUP_NAMES.includes(normalizedName)) {
        throw new BadRequestError(
          'Group name "admin", "everyone", or "standard" cannot be used',
        );
      }
      if (await groups.nameTaken(orgId, normalizedName, groupId)) {
        throw new BadRequestError(GROUP_EXISTS_MESSAGE);
      }
    }

    const renamed = await saveGroupName(() =>
      groups.rename(orgId, groupId, normalizedName),
    );
    if (!renamed) {
      throw new NotFoundError('User group not found');
    }

    res.status(200).json(renamed);
  }

  async deleteGroup(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const groupId = req.params.groupId as string;
    const orgId = req.user?.orgId as string;
    const userId = req.user?.userId;

    const groups = getGroupRepository();
    const group = await groups.getLive(orgId, groupId);

    if (!group) {
      throw new NotFoundError('User group not found');
    }

    if (group.type !== 'custom') {
      throw new ForbiddenError('Only custom groups can be deleted');
    }

    const deleted = await groups.softDelete(orgId, groupId, userId as string);

    res.status(200).json(deleted);
  }

  async addUsersToGroups(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const { userIds, groupIds } = req.body;
    const orgId = req.user?.orgId as string;

    if (!userIds || !Array.isArray(userIds) || userIds.length === 0) {
      throw new BadRequestError('userIds array is required');
    }

    if (!groupIds || !Array.isArray(groupIds) || groupIds.length === 0) {
      throw new BadRequestError('groupIds array is required');
    }

    const modified = await getGroupRepository().addMembers(
      orgId,
      groupIds as string[],
      userIds as string[],
    );

    if (modified === 0) {
      throw new BadRequestError('No groups found or updated');
    }

    res.status(200).json({ message: 'Users added to groups successfully' });
  }

  async removeUsersFromGroups(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const { userIds, groupIds } = req.body;
    const orgId = req.user?.orgId as string;

    if (!userIds || !Array.isArray(userIds) || userIds.length === 0) {
      throw new BadRequestError('User IDs are required');
    }

    if (!groupIds || !Array.isArray(groupIds) || groupIds.length === 0) {
      throw new BadRequestError('Group IDs are required');
    }

    const modified = await getGroupRepository().removeMembers(
      orgId,
      groupIds as string[],
      userIds as string[],
    );

    if (modified === 0) {
      throw new BadRequestError('No groups found or updated');
    }

    res.status(200).json({ message: 'Users removed from groups successfully' });
  }

  async getUsersInGroup(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const groupId = req.params.groupId as string;
    const orgId = req.user?.orgId as string;
    const { page, limit, skip } = this.parsePagination(req);
    const search = (req.query.search as string)?.trim();

    const group = await getGroupRepository().getLive(orgId, groupId);

    if (!group) {
      throw new NotFoundError('Group not found');
    }

    const allUserIds = group.users;

    // This page carries each member's id, name and email, so a service account
    // in the group would be fully identifiable — and its id is what binds an
    // agent to it. Applied to the query rather than the page, so the count
    // agrees with what comes back.
    const includeServiceAccounts =
      req.user?.userId !== undefined &&
      (await isUserOrgAdmin(String(req.user.userId), orgId));
    const { rows: userDocs, total: totalCount } =
      await getUserRepository().listMembersPage(orgId, allUserIds, {
        search,
        skip,
        limit,
        includeServiceAccounts,
      });

    // Bulk-fetch DPs for this page of users
    const pageUserIds = userDocs.map((u) => u._id);
    const dpMap = new Map<string, string>();
    if (pageUserIds.length > 0) {
      const dpDocs = await getPictureRepository().listSet(orgId, pageUserIds);

      for (const dp of dpDocs) {
        if (dp.userId && dp.pic) {
          const mime = dp.mimeType || 'image/jpeg';
          dpMap.set(dp.userId, `data:${mime};base64,${dp.pic}`);
        }
      }
    }

    const users = userDocs.map((u) => ({
      _id: u._id,
      fullName: u.fullName ?? null,
      email: u.email ?? null,
      profilePicture: dpMap.get(u._id) ?? null,
    }));

    res.status(200).json({
      users,
      pagination: buildPaginationMetadata(totalCount, page, limit),
    });
  }

  async getGroupsForUser(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const userId = req.params.userId as string;
    const orgId = req.user?.orgId as string;

    const groups = await getGroupRepository().groupsOfUser(orgId, userId);

    res.status(200).json(groups);
  }

  async getGroupStatistics(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const stats = await getGroupRepository().stats(req.user?.orgId as string);

    res.status(200).json(stats);
  }
}
