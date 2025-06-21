import mongoose from 'mongoose';
import { Response } from 'express';
import { Users } from '../schema/users.schema'; // Adjust path as needed
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import {
  BadRequestError,
  ForbiddenError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { injectable } from 'inversify';
import { groupTypes, UserGroups } from '../schema/userGroup.schema';

@injectable()
export class UserGroupController {
  constructor() {}

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

    res.status(201).json(group);
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

    res.status(200).json(groups);
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

    res.json(userGroup);
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
      { new: true },
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
      { new: true },
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

    const group = await UserGroups.findOne({
      _id: groupId,
      orgId,
      isDeleted: false,
    });

    if (!group) {
      throw new NotFoundError('Group not found');
    }

    res.status(200).json({ users: group.users });
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
    }).select('name type');

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
