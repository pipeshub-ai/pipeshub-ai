import mongoose from 'mongoose';
import {
  UserGroup,
  UserGroups,
} from '../../../modules/user_management/schema/userGroup.schema';
import { asQueryFilter } from '../../utils/mongo-filter';
import { escapeRegExp } from '../../../utils/escape-regexp';
import { mapMongoError, sessionOf } from '../mongo';
import type { Tx } from '../uow';
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
      // without a sort MongoDB returns whatever order the chosen index gives, and skip/limit pages can repeat or miss groups
      UserGroups.find(filter)
        .sort({ _id: 1 })
        .skip(q.skip)
        .limit(q.limit)
        .lean()
        .exec(),
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

  async listLiveByIds(
    orgId: string,
    ids: readonly string[],
  ): Promise<GroupSummaryDto[]> {
    const valid = [...new Set(ids)].filter((id) =>
      mongoose.isValidObjectId(id),
    );
    if (valid.length === 0 || !mongoose.isValidObjectId(orgId)) return [];
    const rows = await UserGroups.find({
      _id: { $in: valid },
      orgId,
      isDeleted: false,
    })
      .select('_id name type')
      .sort({ _id: 1 })
      .lean()
      .exec();
    return rows.map((r) => toPlain<GroupSummaryDto>(r));
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

  async holdLiveMembership(
    orgId: string,
    groupIds: readonly string[],
    userId: string,
    tx?: Tx,
  ): Promise<boolean> {
    const groups = groupIds.filter((id) => mongoose.isValidObjectId(id));
    if (
      groups.length === 0 ||
      !mongoose.isValidObjectId(orgId) ||
      !mongoose.isValidObjectId(userId)
    )
      return false;
    // A write in the transaction: a concurrent removal from the same group document conflicts with it.
    const fenced = await UserGroups.updateOne(
      { _id: { $in: groups }, orgId, isDeleted: { $ne: true }, users: userId },
      { $inc: { approvalDecisionFence: 1 } },
      { session: sessionOf(tx), strict: false },
    ).exec();
    return fenced.matchedCount > 0;
  }

  async groupsOfUser(
    orgId: string,
    userId: string,
  ): Promise<GroupSummaryDto[]> {
    const rows = await UserGroups.find({
      orgId,
      users: { $in: [userId] },
      isDeleted: false,
    })
      .select('name type')
      .sort({ _id: 1 });
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
      .sort({ _id: 1 })
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
      .sort({ _id: 1 })
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
      { $sort: { _id: 1 } },
    ]);
  }
}
