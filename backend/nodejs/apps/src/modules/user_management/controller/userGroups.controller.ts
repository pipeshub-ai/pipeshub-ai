import mongoose from 'mongoose';
import { Response } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import {
  BadRequestError,
  ForbiddenError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { injectable } from 'inversify';
import { groupTypes, UserGroups } from '../schema/userGroup.schema';
import { sendValidatedJson } from '../../../utils/response-validator';
import {
  AddUsersToGroupsResponseSchema,
  CreateUserGroupResponseSchema,
  GetAllUserGroupsResponseSchema,
  GetGroupStatisticsResponseSchema,
  GetGroupsForUserResponseSchema,
  GetUsersInGroupResponseSchema,
  RemoveUsersFromGroupsResponseSchema,
  UserGroupDocumentResponseSchema,
} from '../validation/userGroup-validators';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';

@injectable()
export class UserGroupController {
  constructor() {}

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
    if (name === 'admin' || type === 'admin') {
      throw new BadRequestError('this type of group cannot be created');
    }

    if (!groupTypes.find((groupType) => groupType === type)) {
      throw new BadRequestError('type(Type of the Group) unknown');
    }

    const groupWithSameName = await UserGroups.findOne({
      name,
      isDeleted: false,
    });

    if (groupWithSameName) {
      throw new BadRequestError('Group already exists');
    }

    const newGroup = new UserGroups({
      name: name,
      type: type,
      orgId: req.user?.orgId,
      users: [],
    });

    const group = await newGroup.save();

    sendValidatedJson(
      res,
      CreateUserGroupResponseSchema,
      group.toJSON(),
      HTTP_STATUS.CREATED,
    );
  }

  async getAllUserGroups(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const orgId = req.user?.orgId;

    const groups = await UserGroups.find({
      orgId,
      isDeleted: false,
    })
      .lean()
      .exec();

    sendValidatedJson(
      res,
      GetAllUserGroupsResponseSchema,
      groups,
      HTTP_STATUS.OK,
    );
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

    sendValidatedJson(
      res,
      UserGroupDocumentResponseSchema,
      userGroup,
      HTTP_STATUS.OK,
    );
  }

  async updateGroup(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const { id } = req.params;
    const { name } = req.body;
    const orgId = req.user?.orgId;

    if (!name) {
      throw new BadRequestError('New name is required');
    }

    const group = await UserGroups.findOne({
      _id: id,
      orgId,
      isDeleted: false,
    });

    if (!group) {
      throw new NotFoundError('User group not found');
    }

    if (group.type == 'admin' || group.type == 'everyone') {
      throw new ForbiddenError('Not Allowed');
    }

    group.name = name;

    await group.save();

    sendValidatedJson(
      res,
      UserGroupDocumentResponseSchema,
      group.toJSON(),
      HTTP_STATUS.OK,
    );
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

    sendValidatedJson(
      res,
      UserGroupDocumentResponseSchema,
      group.toJSON(),
      HTTP_STATUS.OK,
    );
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
      { new: true },
    );

    if (updatedGroups.modifiedCount === 0) {
      throw new BadRequestError('No groups found or updated');
    }

    sendValidatedJson(
      res,
      AddUsersToGroupsResponseSchema,
      { message: 'Users added to groups successfully' },
      HTTP_STATUS.OK,
    );
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
      { new: true },
    );

    if (updatedGroups.modifiedCount === 0) {
      throw new BadRequestError('No groups found or updated');
    }

    sendValidatedJson(
      res,
      RemoveUsersFromGroupsResponseSchema,
      { message: 'Users removed from groups successfully' },
      HTTP_STATUS.OK,
    );
  }

  async getUsersInGroup(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const { groupId } = req.params;
    const orgId = req.user?.orgId;

    const group = await UserGroups.findOne({
      _id: groupId,
      orgId,
      isDeleted: false,
    });

    if (!group) {
      throw new NotFoundError('Group not found');
    }

    const users = (group.users ?? []).map((id: unknown) =>
      typeof id === 'string' ? id : String(id),
    );
    sendValidatedJson(
      res,
      GetUsersInGroupResponseSchema,
      { users },
      HTTP_STATUS.OK,
    );
  }

  async getGroupsForUser(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const { userId } = req.params;
    const orgId = req.user?.orgId;

    const groups = await UserGroups.find({
      orgId,
      users: { $in: [userId] },
      isDeleted: false,
    })
      .select('name type')
      .lean()
      .exec();

    sendValidatedJson(
      res,
      GetGroupsForUserResponseSchema,
      groups,
      HTTP_STATUS.OK,
    );
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

    sendValidatedJson(
      res,
      GetGroupStatisticsResponseSchema,
      stats,
      HTTP_STATUS.OK,
    );
  }
}
