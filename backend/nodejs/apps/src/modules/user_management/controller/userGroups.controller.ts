import mongoose from 'mongoose';
import { Response } from 'express';
import { Users, User } from '../schema/users.schema';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import {
  BadRequestError,
  ForbiddenError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { injectable } from 'inversify';
import { groupTypes, UserGroup, UserGroups } from '../schema/userGroup.schema';
import { asQueryFilter } from '../../../libs/utils/mongo-filter';
import { UserDisplayPicture } from '../schema/userDp.schema';
import { safeParsePagination } from '../../../utils/safe-integer';
import { buildPaginationMetadata } from '../../enterprise_search/utils/utils';
import { escapeRegExp } from '../../../utils/escape-regexp';
import type { UserGroupFilter, UserFilter } from '../types/user_management.types';
import { isUserOrgAdmin } from '../services/user-admin.service';

const RESERVED_GROUP_NAMES = ['admin', 'everyone', 'standard'];
const GROUP_EXISTS_MESSAGE = 'Group already exists';

// Two requests can both pass the findOne check; the unique index on
// (orgId, name) then rejects the second write with code 11000.
function isDuplicateGroupNameError(error: unknown): boolean {
  const e = error as
    | { code?: unknown; keyPattern?: Record<string, unknown> }
    | null
    | undefined;
  return e?.code === 11000 && e.keyPattern?.name !== undefined;
}

async function saveGroupName<T extends { save(): Promise<T> }>(
  group: T,
): Promise<T> {
  try {
    return await group.save();
  } catch (error) {
    if (isDuplicateGroupNameError(error)) {
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
    const users = await Users.find({
      orgId: req.user?.orgId,
      isDeleted: false,
    });
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
    if (RESERVED_GROUP_NAMES.includes(name) || RESERVED_GROUP_NAMES.includes(type)) {
      throw new BadRequestError('Group name or type "admin", "everyone", or "standard" cannot be created');
    }

    if (!groupTypes.find((groupType) => groupType === type)) {
      throw new BadRequestError('type(Type of the Group) unknown');
    }

    const groupWithSameName = await UserGroups.findOne({
      name,
      orgId: req.user?.orgId,
      isDeleted: false,
    });

    if (groupWithSameName) {
      throw new BadRequestError(GROUP_EXISTS_MESSAGE);
    }

    const newGroup = new UserGroups({
      name: name,
      type: type,
      orgId: req.user?.orgId,
      users: [],
    });

    const group = await saveGroupName(newGroup);

    res.status(201).json(group);
  }

  async getAllUserGroups(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const orgId = req.user?.orgId;
    const { page, limit, skip } = this.parsePagination(req);
    const search = (req.query.search as string)?.trim();
    const createdAfter = (req.query.createdAfter as string)?.trim();
    const createdBefore = (req.query.createdBefore as string)?.trim();

    const filter: UserGroupFilter = { orgId, isDeleted: false };
    if (search) {
      filter.name = { $regex: escapeRegExp(search), $options: 'i' };
    }
    if (createdAfter || createdBefore) {
      const dateFilter: Record<string, Date> = {};
      if (createdAfter) dateFilter.$gte = new Date(createdAfter);
      if (createdBefore) {
        const beforeDate = new Date(createdBefore);
        beforeDate.setHours(23, 59, 59, 999);
        dateFilter.$lte = beforeDate;
      }
      (filter as any).createdAt = dateFilter;
    }

    const [groups, totalCount] = await Promise.all([
      UserGroups.find(asQueryFilter<UserGroup>(filter))
        .skip(skip)
        .limit(limit)
        .lean()
        .exec(),
      UserGroups.countDocuments(asQueryFilter<UserGroup>(filter)),
    ]);

    // Return userCount instead of the full users array
    const result = groups.map((group) => {
      const timestamps = group as typeof group & { createdAt?: string; updatedAt?: string };
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
    const groupId = req.params.groupId;
    const orgId = req.user?.orgId;

    const userGroup = await UserGroups.findOne({
      _id: groupId,
      orgId,
    })
      .lean()
      .exec();

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
    orgId: unknown,
    ids: readonly mongoose.Types.ObjectId[],
  ): Promise<mongoose.Types.ObjectId[]> {
    const members = [...ids];
    if (members.length === 0) return members;

    const accounts = (await Users.find({
      _id: { $in: members },
      kind: 'service',
    })
      .select('_id')
      .lean()
      .exec()) as Array<{ _id: mongoose.Types.ObjectId }>;
    if (accounts.length === 0) return members;

    const isAdmin =
      req.user?.userId !== undefined &&
      (await isUserOrgAdmin(String(req.user.userId), String(orgId)));
    if (isAdmin) return members;

    const hidden = new Set(accounts.map((u) => u._id.toString()));
    return members.filter((id) => !hidden.has(id.toString()));
  }

  async updateGroup(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const { groupId } = req.params;
    const { name } = req.body as { name?: string };
    const orgId = req.user?.orgId;

    if (!name) {
      throw new BadRequestError('New name is required');
    }

    const normalizedName = name.trim();

    if (!normalizedName) {
      throw new BadRequestError('New name is required');
    }

    const group = await UserGroups.findOne({
      _id: groupId,
      orgId,
      isDeleted: false,
    });

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
      const groupWithSameName = await UserGroups.findOne({
        _id: { $ne: groupId },
        name: normalizedName,
        orgId,
        isDeleted: false,
      });
      if (groupWithSameName) {
        throw new BadRequestError(GROUP_EXISTS_MESSAGE);
      }
    }

    group.name = normalizedName;

    await saveGroupName(group);

    res.status(200).json(group);
  }

  async deleteGroup(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const { groupId } = req.params;
    const orgId = req.user?.orgId;
    const userId = req.user?.userId;

    const group = await UserGroups.findOne({
      _id: groupId,
      orgId,
      isDeleted: false,
    }).exec();

    if (!group) {
      throw new NotFoundError('User group not found');
    }

    if (group.type !== 'custom') {
      throw new ForbiddenError('Only custom groups can be deleted');
    }

    group.isDeleted = true;
    group.deletedBy = userId;

    await group.save();

    res.status(200).json(group);
  }

  async addUsersToGroups(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const { userIds, groupIds } = req.body;
    const orgId = req.user?.orgId;

    if (!userIds || !Array.isArray(userIds) || userIds.length === 0) {
      throw new BadRequestError('userIds array is required');
    }

    if (!groupIds || !Array.isArray(groupIds) || groupIds.length === 0) {
      throw new BadRequestError('groupIds array is required');
    }

    const updatedGroups = await UserGroups.updateMany(
      { _id: { $in: groupIds }, orgId, isDeleted: false },
      { $addToSet: { users: { $each: userIds } } },
    );

    if (updatedGroups.modifiedCount === 0) {
      throw new BadRequestError('No groups found or updated');
    }

    res.status(200).json({ message: 'Users added to groups successfully' });
  }

  async removeUsersFromGroups(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const { userIds, groupIds } = req.body;
    const orgId = req.user?.orgId;

    if (!userIds || !Array.isArray(userIds) || userIds.length === 0) {
      throw new BadRequestError('User IDs are required');
    }

    if (!groupIds || !Array.isArray(groupIds) || groupIds.length === 0) {
      throw new BadRequestError('Group IDs are required');
    }

    const updatedGroups = await UserGroups.updateMany(
      { _id: { $in: groupIds }, orgId, isDeleted: false },
      { $pullAll: { users: userIds } },
    );

    if (updatedGroups.modifiedCount === 0) {
      throw new BadRequestError('No groups found or updated');
    }

    res.status(200).json({ message: 'Users removed from groups successfully' });
  }

  async getUsersInGroup(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const { groupId } = req.params;
    const orgId = req.user?.orgId;
    const { page, limit, skip } = this.parsePagination(req);
    const search = (req.query.search as string)?.trim();

    const group = await UserGroups.findOne({
      _id: groupId,
      orgId,
      isDeleted: false,
    }).lean().exec();

    if (!group) {
      throw new NotFoundError('Group not found');
    }

    const allUserIds = group.users.map((u) => u.toString());

    // Fetch user details with optional search filter
    const userFilter: UserFilter = { _id: { $in: allUserIds }, isDeleted: { $ne: true } };
    // This page carries each member's id, name and email, so a service account
    // in the group would be fully identifiable — and its id is what binds an
    // agent to it. Applied to the filter rather than the page, so the count
    // agrees with what comes back.
    const maySeeServiceAccounts =
      req.user?.userId !== undefined &&
      (await isUserOrgAdmin(String(req.user.userId), String(orgId)));
    if (!maySeeServiceAccounts) {
      userFilter.kind = { $ne: 'service' };
    }
    if (search) {
      const escaped = escapeRegExp(search);
      userFilter.$or = [
        { fullName: { $regex: escaped, $options: 'i' } },
        { email: { $regex: escaped, $options: 'i' } },
      ];
    }

    const [userDocs, totalCount] = await Promise.all([
      Users.find(asQueryFilter<User>(userFilter))
        .select('fullName firstName lastName email')
        .skip(skip)
        .limit(limit)
        .lean()
        .exec(),
      Users.countDocuments(asQueryFilter<User>(userFilter)),
    ]);

    // Bulk-fetch DPs for this page of users
    const pageUserIds = userDocs.map((u) => u._id.toString());
    const dpMap = new Map<string, string>();
    if (pageUserIds.length > 0) {
      const dpDocs = await UserDisplayPicture.find({
        orgId,
        userId: { $in: pageUserIds },
        pic: { $ne: null },
      }).lean().exec();

      for (const dp of dpDocs) {
        if (dp.userId && dp.pic) {
          const mime = dp.mimeType || 'image/jpeg';
          dpMap.set(dp.userId.toString(), `data:${mime};base64,${dp.pic}`);
        }
      }
    }

    const users = userDocs.map((u) => ({
      _id: u._id.toString(),
      fullName: u.fullName ?? null,
      email: u.email ?? null,
      profilePicture: dpMap.get(u._id.toString()) ?? null,
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
    const { userId } = req.params;
    const orgId = req.user?.orgId;

    const groups = await UserGroups.find(
      asQueryFilter<UserGroup>({
        orgId,
        users: { $in: [userId] },
        isDeleted: false,
      }),
    ).select('name type');

    res.status(200).json(groups);
  }

  async getGroupStatistics(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const orgId = new mongoose.Types.ObjectId(req.user?.orgId);

    const stats = await UserGroups.aggregate([
      { $match: { orgId, isDeleted: false } },
      {
        $group: {
          _id: '$name',
          count: { $sum: 1 },
          totalUsers: { $sum: { $size: '$users' } },
          avgUsers: { $avg: { $size: '$users' } },
        },
      },
    ]);

    res.status(200).json(stats);
  }
}
