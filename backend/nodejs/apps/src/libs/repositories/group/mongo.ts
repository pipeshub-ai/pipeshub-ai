import mongoose from 'mongoose';
import {
  UserGroup,
  UserGroups,
} from '../../../modules/user_management/schema/userGroup.schema';
import { asQueryFilter } from '../../utils/mongo-filter';
import { escapeRegExp } from '../../../utils/escape-regexp';
import { mapMongoError } from '../mongo';
import { toPlain } from '../plain';
import type {
  GroupDto,
  GroupListQuery,
  GroupRepository,
  GroupStat,
  GroupSummaryDto,
  GroupType,
} from './types';

const dto = (row: unknown): GroupDto => toPlain<GroupDto>(row);

export class MongoGroupRepository implements GroupRepository {
  async list(
    orgId: string,
    q: GroupListQuery,
  ): Promise<{ rows: GroupDto[]; total: number }> {
    const filter: Record<string, unknown> = { orgId, isDeleted: false };
    if (q.search !== undefined && q.search !== '') {
      filter.name = { $regex: escapeRegExp(q.search), $options: 'i' };
    }
    const after = q.createdAfter !== undefined && q.createdAfter !== '';
    const before = q.createdBefore !== undefined && q.createdBefore !== '';
    if (after || before) {
      const dateFilter: Record<string, Date> = {};
      if (after) dateFilter.$gte = new Date(q.createdAfter as string);
      if (before) {
        const beforeDate = new Date(q.createdBefore as string);
        beforeDate.setHours(23, 59, 59, 999);
        dateFilter.$lte = beforeDate;
      }
      filter.createdAt = dateFilter;
    }
    const [rows, total] = await Promise.all([
      UserGroups.find(filter).skip(q.skip).limit(q.limit).lean().exec(),
      UserGroups.countDocuments(filter),
    ]);
    return { rows: rows.map(dto), total };
  }

  async get(orgId: string, id: string): Promise<GroupDto | null> {
    const row = await UserGroups.findOne({ _id: id, orgId }).lean().exec();
    return row ? dto(row) : null;
  }

  async getLive(orgId: string, id: string): Promise<GroupDto | null> {
    const row = await UserGroups.findOne({ _id: id, orgId, isDeleted: false })
      .lean()
      .exec();
    return row ? dto(row) : null;
  }

  async nameTaken(
    orgId: string,
    name: string,
    exceptId?: string,
  ): Promise<boolean> {
    return !!(await UserGroups.findOne({
      ...(exceptId === undefined ? {} : { _id: { $ne: exceptId } }),
      name,
      orgId,
      isDeleted: false,
    }));
  }

  async create(
    orgId: string,
    input: { name: string; type: GroupType },
  ): Promise<GroupDto> {
    const group = new UserGroups({
      name: input.name,
      type: input.type,
      orgId,
      users: [],
    });
    try {
      await group.save();
    } catch (error) {
      throw mapMongoError(error);
    }
    return dto(group);
  }

  async rename(
    orgId: string,
    id: string,
    name: string,
  ): Promise<GroupDto | null> {
    const group = await UserGroups.findOne({
      _id: id,
      orgId,
      isDeleted: false,
    });
    if (!group) return null;
    group.name = name;
    try {
      await group.save();
    } catch (error) {
      throw mapMongoError(error);
    }
    return dto(group);
  }

  async softDelete(
    orgId: string,
    id: string,
    deletedBy?: string,
  ): Promise<GroupDto | null> {
    const group = await UserGroups.findOne({
      _id: id,
      orgId,
      isDeleted: false,
    }).exec();
    if (!group) return null;
    group.isDeleted = true;
    group.deletedBy = deletedBy as unknown as mongoose.Types.ObjectId;
    await group.save();
    return dto(group);
  }

  async addMembers(
    orgId: string,
    groupIds: string[],
    userIds: string[],
  ): Promise<number> {
    const res = await UserGroups.updateMany(
      { _id: { $in: groupIds }, orgId, isDeleted: false },
      { $addToSet: { users: { $each: userIds } } },
    );
    return res.modifiedCount;
  }

  async removeMembers(
    orgId: string,
    groupIds: string[],
    userIds: string[],
  ): Promise<number> {
    const res = await UserGroups.updateMany(
      { _id: { $in: groupIds }, orgId, isDeleted: false },
      { $pullAll: { users: userIds } },
    );
    return res.modifiedCount;
  }

  async addMemberToGroups(
    orgId: string,
    groupIds: string[],
    userId: string,
  ): Promise<void> {
    await UserGroups.updateMany(
      { _id: { $in: groupIds }, orgId },
      { $addToSet: { users: userId } },
    );
  }

  async addToEveryone(
    orgId: string,
    userId: string,
    opts?: { liveOnly?: boolean },
  ): Promise<void> {
    await UserGroups.updateOne(
      { orgId, type: 'everyone', ...(opts?.liveOnly && { isDeleted: false }) },
      { $addToSet: { users: userId } },
    );
  }

  async removeFromEveryone(orgId: string, userId: string): Promise<void> {
    await UserGroups.updateOne(
      { orgId, type: 'everyone' },
      { $pull: { users: userId } },
    );
  }

  async removeFromAll(
    orgId: string,
    userId: string,
    opts?: { everyGroup?: boolean },
  ): Promise<void> {
    await UserGroups.updateMany(
      { orgId, ...(opts?.everyGroup ? {} : { users: userId }) },
      { $pull: { users: userId } },
    );
  }

  async groupsOfUser(
    orgId: string,
    userId: string,
  ): Promise<GroupSummaryDto[]> {
    const rows = await UserGroups.find({
      orgId,
      users: { $in: [userId] },
      isDeleted: false,
    }).select('name type');
    return rows.map((r) => toPlain<GroupSummaryDto>(r));
  }

  async memberIdsOfTypes(orgId: string, types: string[]): Promise<string[]> {
    const groups = await UserGroups.find(
      asQueryFilter<UserGroup>({
        orgId: new mongoose.Types.ObjectId(orgId),
        type: { $in: types },
        isDeleted: { $ne: true },
      }),
    )
      .select('users')
      .lean();
    return groups.flatMap((g) =>
      ((g as { users?: unknown[] }).users ?? []).map(String),
    );
  }

  async summariesForUsers(
    orgId: string,
    userIds: string[],
  ): Promise<Array<GroupSummaryDto & { users: string[] }>> {
    const rows = await UserGroups.find({
      orgId: new mongoose.Types.ObjectId(orgId),
      isDeleted: false,
      users: {
        $in: userIds.map((id) => new mongoose.Types.ObjectId(id)),
      },
    })
      .select('_id name type users')
      .lean()
      .exec();
    return rows.map((r) => toPlain<GroupSummaryDto & { users: string[] }>(r));
  }

  async stats(orgId: string): Promise<GroupStat[]> {
    return UserGroups.aggregate([
      {
        $match: { orgId: new mongoose.Types.ObjectId(orgId), isDeleted: false },
      },
      {
        $group: {
          _id: '$name',
          count: { $sum: 1 },
          totalUsers: { $sum: { $size: '$users' } },
          avgUsers: { $avg: { $size: '$users' } },
        },
      },
    ]);
  }
}
