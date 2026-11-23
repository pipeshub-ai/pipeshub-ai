import { randomUUID } from 'crypto';
import mongoose from 'mongoose';
import { Users } from '../../../modules/user_management/schema/users.schema';
import { UserGroups } from '../../../modules/user_management/schema/userGroup.schema';
import { Org } from '../../../modules/user_management/schema/org.schema';
import { UserCredentials } from '../../../modules/auth/schema/userCredentials.schema';
import { escapeRegExp } from '../../../utils/escape-regexp';
import { mapMongoError, sessionOf } from '../mongo';
import { ServiceUnavailableError } from '../../errors/http.errors';
import { Logger } from '../../services/logger.service';
import { toPlain } from '../plain';
import type { Tx } from '../uow';
import type {
  AdminLease,
  DirectoryQuery,
  MemberSummaryDto,
  RestoreServiceAccountInput,
  UserAccountState,
  UserAuthSnapshot,
  UserDto,
  UserFields,
  UserRepository,
  UserRoleSnapshot,
  UserRole,
  UserWithGroupsDto,
} from './types';

const oid = (id: string): mongoose.Types.ObjectId =>
  new mongoose.Types.ObjectId(id);

const dto = (row: unknown): UserDto => toPlain<UserDto>(row);

/**
 * These queries answer "who administers this organisation", and both callers
 * mean people by it: one notifies administrators, the other refuses to demote
 * the last one. A service account cannot hold the admin role -- the schema
 * refuses it -- but a row written straight to the database could, and counting
 * it would let the last person who can sign in be demoted.
 *
 * `$ne` rather than a match on 'human', because records created before `kind`
 * existed have no value there at all.
 */
const NOT_A_SERVICE_ACCOUNT = { kind: { $ne: 'service' } } as const;

/**
 * How long a standalone-Mongo admin-role lease lasts, and how long a change waits for
 * a held one before answering busy. Read at call time, so tests can shorten them.
 */
export const adminLeaseTiming = { leaseMs: 30_000, waitMs: 5_000 };

const ADMIN_LEASE_BUSY_MESSAGE =
  'Another administrator change for this organisation is in progress. Try again in a moment.';

/** Takes the lease when it is free or expired; expiry is judged by the server's clock. */
const tryTakeAdminLease = async (
  orgId: string,
  holder: string,
): Promise<boolean> =>
  !!(await Org.findOneAndUpdate(
    // A missing `until` sorts below any date, so a free lease matches too.
    {
      _id: orgId,
      isDeleted: { $ne: true },
      $expr: { $lt: ['$adminRoleLease.until', '$$NOW'] },
    },
    [
      {
        $set: {
          adminRoleLease: {
            holder,
            until: { $add: ['$$NOW', adminLeaseTiming.leaseMs] },
          },
        },
      },
    ],
    { projection: { _id: 1 }, lean: true, updatePipeline: true },
  ));

/** True once taken; false when the organisation is gone (nothing left to serialise). */
const takeAdminLease = async (
  orgId: string,
  holder: string,
): Promise<boolean> => {
  const deadline = Date.now() + adminLeaseTiming.waitMs;
  let pauseMs = 25;
  for (;;) {
    if (await tryTakeAdminLease(orgId, holder)) return true;
    if (!(await Org.exists({ _id: orgId, isDeleted: { $ne: true } }))) {
      return false;
    }
    if (Date.now() >= deadline) {
      throw new ServiceUnavailableError(ADMIN_LEASE_BUSY_MESSAGE, {
        retryAfter: '5',
      });
    }
    await new Promise((resolve) =>
      setTimeout(resolve, pauseMs * (0.8 + Math.random() * 0.4)),
    );
    pauseMs = Math.min(pauseMs * 2, 200);
  }
};

/** True, and renewed for a full term, while `holder` still holds the lease by the server's clock. */
const renewAdminLease = async (
  orgId: string,
  holder: string,
): Promise<boolean> =>
  !!(await Org.findOneAndUpdate(
    {
      _id: orgId,
      'adminRoleLease.holder': holder,
      $expr: { $gt: ['$adminRoleLease.until', '$$NOW'] },
    },
    [
      {
        $set: {
          'adminRoleLease.until': {
            $add: ['$$NOW', adminLeaseTiming.leaseMs],
          },
        },
      },
    ],
    { projection: { _id: 1 }, lean: true, updatePipeline: true },
  ));

/** Gives back only our own lease; a lease that expired under us was the overrun to report. */
const releaseAdminLease = async (
  orgId: string,
  holder: string,
): Promise<void> => {
  try {
    const { matchedCount } = await Org.updateOne(
      { _id: orgId, 'adminRoleLease.holder': holder },
      { $unset: { adminRoleLease: 1 } },
    );
    if (matchedCount === 0) {
      Logger.getInstance().warn(
        'Admin-role lease expired before the change finished; another admin change may have overlapped it',
        { orgId },
      );
    }
  } catch (error) {
    // The change itself is done or failed already; an unreleased lease expires on its own.
    Logger.getInstance().warn(
      'Admin-role lease could not be released; it will expire',
      {
        orgId,
        error: error instanceof Error ? error.message : String(error),
      },
    );
  }
};

export class MongoUserRepository implements UserRepository {
  async getLive(orgId: string, id: string): Promise<UserDto | null> {
    const row = await Users.findOne({ _id: id, orgId, isDeleted: false })
      .lean()
      .exec();
    return row ? dto(row) : null;
  }

  async getForEdit(orgId: string, id: string): Promise<UserDto | null> {
    const row = await Users.findOne({ orgId, _id: id, isDeleted: false });
    return row ? dto(row) : null;
  }

  async getEmail(
    orgId: string,
    id: string,
  ): Promise<Pick<UserDto, 'email'> | null> {
    const row = await Users.findOne({ _id: id, orgId, isDeleted: false })
      .select('email')
      .lean()
      .exec();
    return row ? toPlain<Pick<UserDto, 'email'>>(row) : null;
  }

  async getAny(orgId: string, id: string): Promise<UserDto | null> {
    const row = await Users.findOne({ orgId, _id: id });
    return row ? dto(row) : null;
  }

  async getLiveById(id: string): Promise<UserDto | null> {
    const row = await Users.findOne({ _id: id, isDeleted: false })
      .lean()
      .exec();
    return row ? dto(row) : null;
  }

  async getAccountState(id: string): Promise<UserAccountState | null> {
    const row = await Users.findOne({ _id: id, isDeleted: false })
      .select('-_id kind isDisabled')
      .lean()
      .exec();
    return row ? toPlain<UserAccountState>(row) : null;
  }

  async getAuthSnapshot(
    orgId: string,
    id: string,
  ): Promise<UserAuthSnapshot | null> {
    const row = await Users.findOne({ _id: id, orgId, isDeleted: false })
      .select('email fullName role isDisabled kind')
      .lean()
      .exec();
    return row ? toPlain<UserAuthSnapshot>(row) : null;
  }

  async getRoleSnapshot(
    orgId: string,
    id: string,
  ): Promise<UserRoleSnapshot | null> {
    const row = await Users.findOne({
      _id: id,
      orgId,
      isDeleted: { $ne: true },
    })
      .select('-_id role kind')
      .lean();
    return row ? toPlain<UserRoleSnapshot>(row) : null;
  }

  async getByEmail(email: string): Promise<UserDto | null> {
    const row = await Users.findOne({ email }).exec();
    return row ? dto(row) : null;
  }

  async getLiveFields<K extends keyof UserDto>(
    orgId: string,
    id: string,
    fields: K[],
    opts: { serviceOnly?: boolean; strict?: boolean } = {},
  ): Promise<Pick<UserDto, K> | null> {
    const row = await Users.findOne({
      _id: opts.strict ? { $eq: id } : id,
      orgId: opts.strict ? { $eq: orgId } : orgId,
      ...(opts.serviceOnly ? { kind: 'service' } : {}),
      isDeleted: false,
    })
      .select(fields.join(' '))
      .lean()
      .exec();
    return row ? toPlain<Pick<UserDto, K>>(row) : null;
  }

  async getByIdOrEmail<K extends keyof UserDto>(
    orgId: string,
    key: { id: string } | { email: string },
    fields: K[],
  ): Promise<Pick<UserDto, K> | null> {
    const query =
      'id' in key
        ? { _id: { $eq: key.id }, orgId: { $eq: orgId } }
        : { email: { $eq: key.email }, orgId: { $eq: orgId } };
    const row = await Users.findOne(query)
      .select(fields.join(' '))
      .lean()
      .exec();
    return row ? toPlain<Pick<UserDto, K>>(row) : null;
  }

  async listSignInEligible(
    orgId: string,
  ): Promise<Array<Pick<UserDto, '_id' | 'fullName' | 'email'>>> {
    const rows = await Users.find({
      orgId,
      isDeleted: { $ne: true },
      hasLoggedIn: true,
      isDisabled: { $ne: true },
      kind: { $ne: 'service' },
    })
      .select('_id fullName email')
      .lean();
    return rows.map((r) =>
      toPlain<Pick<UserDto, '_id' | 'fullName' | 'email'>>(r),
    );
  }

  async listLiveByEmail<K extends keyof UserDto>(
    email: string,
    fields: K[],
    orgId?: string,
  ): Promise<Array<Pick<UserDto, K>>> {
    const rows = await Users.find({
      email,
      isDeleted: false,
      ...(orgId ? { orgId } : {}),
    })
      .select(fields.join(' '))
      .lean()
      .exec();
    return rows.map((r) => toPlain<Pick<UserDto, K>>(r));
  }

  async findActiveByEmail(
    email: string,
  ): Promise<{ _id: string; orgId: string; email: string } | null> {
    const row = await Users.findOne({ email, isDeleted: { $ne: true } })
      .select('_id orgId email')
      .lean()
      .exec();
    return row
      ? toPlain<{ _id: string; orgId: string; email: string }>(row)
      : null;
  }

  async listLiveMembershipsByEmail(
    email: string,
  ): Promise<Array<{ _id: string; orgId: string }>> {
    const rows = await Users.find({ email, isDeleted: false })
      .select('_id orgId')
      .lean()
      .exec();
    return rows.map((r) => toPlain<{ _id: string; orgId: string }>(r));
  }

  async listLiveFieldsByIds<K extends keyof UserDto>(
    ids: string[],
    fields: K[],
  ): Promise<Array<Pick<UserDto, K>>> {
    const rows = await Users.find({ _id: { $in: ids }, isDeleted: false })
      .select(fields.join(' '))
      .lean()
      .exec();
    return rows.map((r) => toPlain<Pick<UserDto, K>>(r));
  }

  async listFieldsByIds<K extends keyof UserDto>(
    ids: string[],
    fields: K[],
  ): Promise<Array<Pick<UserDto, K>>> {
    const rows = await Users.find({ _id: { $in: ids } })
      .select(fields.join(' '))
      .lean()
      .exec();
    return rows.map((r) => toPlain<Pick<UserDto, K>>(r));
  }

  async getOldestId(orgId: string): Promise<string | null> {
    const row = await Users.findOne({
      orgId,
      isDeleted: { $ne: true },
    })
      .sort({ createdAt: 1, _id: 1 })
      .select('_id')
      .lean();
    return row?._id ? String(row._id) : null;
  }

  async getAnyById(id: string): Promise<UserDto | null> {
    const row = await Users.findById(id);
    return row ? dto(row) : null;
  }

  async getLiveByEmail(orgId: string, email: string): Promise<UserDto | null> {
    const row = await Users.findOne({ email, orgId, isDeleted: false });
    return row ? dto(row) : null;
  }

  async getContact(
    id: string,
  ): Promise<Pick<UserDto, '_id' | 'fullName' | 'email'> | null> {
    const row = await Users.findById(id).select('fullName email').lean();
    return row
      ? toPlain<Pick<UserDto, '_id' | 'fullName' | 'email'>>(row)
      : null;
  }

  async listNamesByIds(
    orgId: string,
    ids: string[],
  ): Promise<MemberSummaryDto[]> {
    const rows = await Users.find({
      orgId: oid(orgId),
      isDeleted: false,
      _id: { $in: ids.map(oid) },
    })
      .select('fullName firstName lastName email')
      .lean()
      .exec();
    return rows.map((r) => toPlain<MemberSummaryDto>(r));
  }

  async listLiveByIds(orgId: string, ids: string[]): Promise<UserDto[]> {
    const rows = await Users.find({
      orgId,
      isDeleted: false,
      _id: { $in: ids.map((id) => new mongoose.mongo.ObjectId(id)) },
    })
      .lean()
      .exec();
    return rows.map(dto);
  }

  async listAllLive(orgId: string): Promise<UserDto[]> {
    const rows = await Users.find({ orgId, isDeleted: false });
    return rows.map(dto);
  }

  async listSummariesByIds(
    orgId: string,
    ids: string[],
  ): Promise<
    Array<Pick<UserDto, '_id' | 'hasLoggedIn' | 'fullName' | 'role'>>
  > {
    const rows = await Users.find({
      _id: { $in: ids },
      orgId: oid(orgId),
    })
      .select('_id hasLoggedIn fullName role')
      .lean()
      .exec();
    return rows.map((r) => toPlain<UserDto>(r));
  }

  async listDirectory(
    orgId: string,
    q: DirectoryQuery,
  ): Promise<{ rows: UserDto[]; total: number }> {
    const orgIdObj = oid(orgId);
    const filter: Record<string, unknown> = {
      orgId: orgIdObj,
      isDeleted: { $ne: true },
    };

    const hiddenKinds = [
      ...(q.includeServiceAccounts ? [] : ['service']),
      ...(q.includeSlackOnlyAccounts === true ? [] : ['slack']),
    ];
    if (hiddenKinds.length > 0) {
      filter.kind = { $nin: hiddenKinds };
    }

    if (q.search !== undefined && q.search !== '') {
      const searchRegex = { $regex: escapeRegExp(q.search), $options: 'i' };
      filter.$or = [{ fullName: searchRegex }, { email: searchRegex }];
    }

    const hasLoggedInFilter = q.hasLoggedIn !== undefined;
    const isBlockedFilter = q.blocked !== undefined;

    if (isBlockedFilter) {
      // Resolve blocked user IDs once and apply blocked/non-blocked constraint.
      const blockedCreds = await UserCredentials.find({
        orgId,
        isBlocked: true,
        isDeleted: false,
      })
        .select('userId')
        .lean()
        .exec();
      const blockedIds = blockedCreds.flatMap((c) =>
        c.userId === null || c.userId === undefined || c.userId === ''
          ? []
          : [new mongoose.Types.ObjectId(c.userId)],
      );

      if (q.blocked) {
        const statusConditions: Record<string, unknown>[] = [];
        if (hasLoggedInFilter) {
          statusConditions.push({ hasLoggedIn: q.hasLoggedIn });
        }
        // Always include the blocked constraint when blocked is asked for; an
        // empty $in correctly matches nothing so "Blocked" with zero blocked
        // users returns an empty list rather than the entire org.
        statusConditions.push({ _id: { $in: blockedIds } });
        // Merge with any existing $or (search) using $and
        if (filter.$or !== undefined) {
          filter.$and = [{ $or: filter.$or }, { $or: statusConditions }];
          delete filter.$or;
        } else {
          filter.$or = statusConditions;
        }
      } else {
        if (hasLoggedInFilter) {
          filter.hasLoggedIn = q.hasLoggedIn;
        }
        if (blockedIds.length > 0) {
          filter._id = { ...(filter._id as object), $nin: blockedIds };
        }
      }
    } else if (hasLoggedInFilter) {
      filter.hasLoggedIn = q.hasLoggedIn;
    }

    // Restrict to people in any of the given groups.
    if (q.groupIds !== undefined && q.groupIds.length > 0) {
      const groups = await UserGroups.find({
        _id: { $in: q.groupIds.map((id) => new mongoose.Types.ObjectId(id)) },
        orgId: orgIdObj,
        isDeleted: false,
      })
        .select('users')
        .lean()
        .exec();
      const userIdsInGroups = groups.flatMap((g) =>
        g.users.map((u) => new mongoose.Types.ObjectId(u.toString())),
      );
      filter._id = { ...(filter._id as object), $in: userIdsInGroups };
    }

    for (const field of ['createdAt', 'updatedAt'] as const) {
      const range = q[field];
      if (!range || (range.from === undefined && range.to === undefined))
        continue;
      filter[field] = {
        ...(range.from !== undefined && { $gte: range.from }),
        ...(range.to !== undefined && { $lt: range.to }),
      };
    }

    const [rows, total] = await Promise.all([
      Users.find(filter)
        .sort({ fullName: 1, _id: 1 })
        .skip(q.skip)
        .limit(q.limit)
        .lean()
        .exec(),
      Users.countDocuments(filter),
    ]);
    return { rows: rows.map(dto), total };
  }

  async listWithGroups(
    orgId: string,
    opts: { includeServiceAccounts?: boolean } = {},
  ): Promise<UserWithGroupsDto[]> {
    const orgIdObj = oid(orgId);
    const users = await Users.aggregate([
      {
        $match: {
          orgId: orgIdObj, // Only include users from the same org
          isDeleted: false, // Exclude deleted users
          // Slack-only accounts belong to no group, so they never appear here.
          kind: {
            $nin:
              opts.includeServiceAccounts === true
                ? ['slack']
                : ['service', 'slack'],
          },
        },
      },
      {
        $lookup: {
          from: 'userGroups', // Collection name for user groups
          localField: '_id', // Field in appusers collection
          foreignField: 'users', // Field in appuserGroups collection (array of user IDs)
          as: 'groups', // Resulting array of groups for each user
        },
      },
      {
        $addFields: {
          // Filter groups array to keep only non-deleted groups from same org
          groups: {
            $filter: {
              input: '$groups',
              as: 'group',
              cond: {
                $and: [
                  { $eq: ['$$group.orgId', orgIdObj] },
                  { $ne: ['$$group.isDeleted', true] },
                ],
              },
            },
          },
        },
      },
      {
        $project: {
          _id: 1,
          userId: 1,
          orgId: 1,
          fullName: 1,
          hasLoggedIn: 1,
          groups: {
            $map: {
              input: '$groups',
              as: 'group',
              in: {
                name: '$$group.name',
                type: '$$group.type',
              },
            },
          },
        },
      },
    ]);
    return users.map((u) => toPlain<UserWithGroupsDto>(u));
  }

  async listMembersPage(
    orgId: string,
    ids: string[],
    q: {
      search?: string;
      skip: number;
      limit: number;
      includeServiceAccounts?: boolean;
    },
  ): Promise<{ rows: MemberSummaryDto[]; total: number }> {
    const userFilter: Record<string, unknown> = {
      _id: { $in: ids },
      orgId,
      isDeleted: { $ne: true },
      ...(q.includeServiceAccounts === true ? {} : NOT_A_SERVICE_ACCOUNT),
    };
    if (q.search !== undefined && q.search !== '') {
      const escaped = escapeRegExp(q.search);
      userFilter.$or = [
        { fullName: { $regex: escaped, $options: 'i' } },
        { email: { $regex: escaped, $options: 'i' } },
      ];
    }
    const [rows, total] = await Promise.all([
      Users.find(userFilter)
        .select('fullName firstName lastName email')
        .skip(q.skip)
        .limit(q.limit)
        .lean()
        .exec(),
      Users.countDocuments(userFilter),
    ]);
    return { rows: rows.map((r) => toPlain<MemberSummaryDto>(r)), total };
  }

  async listByEmails(orgId: string, emails: string[]): Promise<UserDto[]> {
    const rows = await Users.find({ email: { $in: emails }, orgId });
    return rows.map(dto);
  }

  async serviceAccountIdsAmong(
    orgId: string,
    ids: string[],
  ): Promise<string[]> {
    if (ids.length === 0) return [];
    const rows = await Users.find({
      orgId,
      _id: { $in: ids },
      kind: 'service',
    })
      .select('_id')
      .lean()
      .exec();
    return (
      rows as Array<{ _id?: { toString(): string } | null } | null>
    ).flatMap((r) => (r?._id == null ? [] : [r._id.toString()]));
  }

  async listAdminIds(orgId: string): Promise<string[]> {
    const rows = await Users.find({
      orgId,
      role: 'admin',
      isDeleted: { $ne: true },
      ...NOT_A_SERVICE_ACCOUNT,
    })
      .select('_id')
      .lean();
    return (
      rows as Array<{ _id?: { toString(): string } | null } | null>
    ).flatMap((r) => (r?._id == null ? [] : [r._id.toString()]));
  }

  async listServiceAccounts(orgId: string): Promise<UserDto[]> {
    const rows = await Users.find({
      orgId,
      kind: 'service',
      isDeleted: false,
    })
      .sort({ createdAt: -1, _id: -1 })
      .exec();
    return rows.map(dto);
  }

  async getServiceAccount(orgId: string, id: string): Promise<UserDto | null> {
    const row = await Users.findOne({
      _id: id,
      orgId,
      kind: 'service',
      isDeleted: false,
    }).exec();
    return row ? dto(row) : null;
  }

  async countActiveAdmins(orgId: string, tx?: Tx): Promise<number> {
    const query = Users.countDocuments({
      orgId,
      role: 'admin',
      isDeleted: { $ne: true },
      ...NOT_A_SERVICE_ACCOUNT,
    });
    const session = sessionOf(tx);
    return session ? query.session(session) : query;
  }

  async existsLiveEmail(email: string): Promise<boolean> {
    return !!(await Users.findOne({ email, isDeleted: false }));
  }

  async emailTakenByOther(
    orgId: string,
    email: string,
    exceptId: string,
  ): Promise<boolean> {
    return !!(await Users.findOne({
      email,
      _id: { $ne: exceptId },
      orgId,
      isDeleted: false,
    }));
  }

  async emailTakenElsewhere(email: string, exceptId: string): Promise<boolean> {
    return !!(await Users.findOne({ email, _id: { $ne: exceptId } }));
  }

  async hasRemovedAccount(orgId: string, email: string): Promise<boolean> {
    return !!(await Users.findOne({ email, orgId, isDeleted: true }));
  }

  async create(orgId: string, fields: UserFields, tx?: Tx): Promise<UserDto> {
    const session = sessionOf(tx);
    const user = new Users({ ...fields, orgId });
    try {
      if (session) await user.save({ session });
      else await user.save();
    } catch (error) {
      throw mapMongoError(error);
    }
    return dto(user);
  }

  async createMany(
    orgId: string,
    rows: UserFields[],
    tx?: Tx,
  ): Promise<UserDto[]> {
    const session = sessionOf(tx);
    const docs = rows.map((r) => ({ ...r, orgId }));
    try {
      // In a transaction the saves must not run in parallel on one session.
      const created = session
        ? await Users.create(docs, { session, ordered: true })
        : await Users.create(docs);
      return created.map(dto);
    } catch (error) {
      throw mapMongoError(error);
    }
  }

  async updateProfile(
    orgId: string,
    id: string,
    fields: UserFields,
    tx?: Tx,
  ): Promise<UserDto | null> {
    const session = sessionOf(tx);
    const user = await Users.findOne({ orgId, _id: id, isDeleted: false });
    if (!user) return null;
    Object.assign(user, fields);
    try {
      if (session) await user.save({ session });
      else await user.save();
    } catch (error) {
      throw mapMongoError(error);
    }
    return dto(user);
  }

  async markDeleted(
    orgId: string,
    id: string,
    extra?: { signedOut?: boolean; deletedBy?: string },
  ): Promise<UserDto | null> {
    const user = await Users.findOne({ orgId, _id: id });
    if (!user) return null;
    user.isDeleted = true;
    if (extra?.signedOut) user.hasLoggedIn = false;
    if (extra && 'deletedBy' in extra) user.deletedBy = extra.deletedBy;
    await user.save();
    return dto(user);
  }

  async upgradeSlackOnly(orgId: string, id: string): Promise<boolean> {
    const result = await Users.updateOne(
      { _id: id, orgId, kind: 'slack' },
      { $set: { kind: 'human' } },
    );
    return result.modifiedCount === 1;
  }

  async restoreByEmails(
    orgId: string,
    emails: string[],
    tx?: Tx,
  ): Promise<UserDto[]> {
    const session = sessionOf(tx);
    const filter = { email: { $in: emails }, isDeleted: true, orgId };
    const restore = { $set: { isDeleted: false } };
    if (session) await Users.updateMany(filter, restore, { session });
    else await Users.updateMany(filter, restore);
    const query = Users.find({
      email: { $in: emails },
      orgId,
      isDeleted: false,
    });
    const rows = session ? await query.session(session) : await query;
    return rows.map(dto);
  }

  async promoteToAdmin(orgId: string, ids: string[], tx?: Tx): Promise<void> {
    // Narrowed to people. The schema refuses to promote a service account, so
    // without this a batch that happened to include one would fail as a whole
    // and take the genuine invites with it.
    const session = sessionOf(tx);
    const filter = {
      _id: { $in: ids },
      orgId,
      kind: { $ne: 'service' as const },
    };
    const promote = { $set: { role: 'admin' as const } };
    if (session) await Users.updateMany(filter, promote, { session });
    else await Users.updateMany(filter, promote);
  }

  async setRole(orgId: string, id: string, role: UserRole): Promise<void> {
    await Users.updateOne(
      { _id: id, orgId, isDeleted: { $ne: true } },
      { $set: { role } },
    );
  }

  /**
   * Serializes concurrent last-admin demotions under snapshot isolation by
   * forcing a write conflict on the shared Org document inside the transaction.
   * Moves to the organisation repository with the rest of `orgs`.
   */
  async lockOrgAdminChanges(orgId: string, tx: Tx): Promise<void> {
    await Org.updateOne(
      { _id: orgId, isDeleted: { $ne: true } },
      { $set: { adminRoleGuardAt: new Date() } },
      { session: sessionOf(tx) },
    );
  }

  async withAdminLease<T>(
    orgId: string,
    fn: (lease: AdminLease) => Promise<T>,
  ): Promise<T> {
    const holder = randomUUID();
    const held = await takeAdminLease(orgId, holder);
    // An organisation that is gone has no admins left to protect.
    const lease: AdminLease = {
      confirm: async () => !held || renewAdminLease(orgId, holder),
    };
    try {
      return await fn(lease);
    } finally {
      if (held) await releaseAdminLease(orgId, holder);
    }
  }

  async deleteById(id: string): Promise<void> {
    await Users.deleteOne({ _id: id });
  }

  async deleteRemoved(orgId: string, id: string): Promise<boolean> {
    const removed = await Users.findOneAndDelete({
      _id: id,
      orgId,
      isDeleted: true,
    })
      .select('_id')
      .lean();
    return !!removed;
  }

  async listRemovedIdsByEmail(orgId: string, email: string): Promise<string[]> {
    const stale = await Users.find({ email, orgId, isDeleted: true })
      .select('_id')
      .lean();
    return stale.map((r) => String(r._id));
  }

  async setDisabledByEmailDomain(
    orgId: string,
    domain: string,
    exceptId: string | undefined,
    disabled: boolean,
  ): Promise<number> {
    const filter: Record<string, unknown> = {
      orgId,
      isDeleted: false,
      email: new RegExp(`@${escapeRegExp(domain)}$`, 'i'),
    };
    if (exceptId !== undefined && mongoose.Types.ObjectId.isValid(exceptId)) {
      filter._id = { $ne: new mongoose.Types.ObjectId(exceptId) };
    }
    const result = await Users.updateMany(filter, {
      $set: { isDisabled: disabled },
    });
    return result.modifiedCount;
  }

  async changeEmail(id: string, email: string): Promise<UserDto | null> {
    const row = await Users.findByIdAndUpdate(
      id,
      { email },
      { returnDocument: 'after' },
    );
    return row ? dto(row) : null;
  }

  async correctEmail(id: string, email: string): Promise<void> {
    await Users.updateOne({ _id: id }, { $set: { email } });
  }

  async restoreServiceAccount(
    orgId: string,
    id: string,
    input: RestoreServiceAccountInput,
  ): Promise<UserDto | null> {
    const restored = await Users.findOneAndUpdate(
      // Narrowed so the update cannot land on another org's row even if the
      // record changed underneath us.
      { _id: id, orgId, kind: 'service', isDeleted: true },
      {
        $set: {
          fullName: input.fullName,
          kind: 'service',
          role: input.role,
          // Restored disabled, and enabled only once the old tokens are gone.
          isDisabled: true,
          isDeleted: false,
          restoreOpId: input.restoreOpId,
          // A description the caller did give.
          ...(input.description === undefined
            ? {}
            : { description: input.description }),
        },
        $unset: {
          deletedBy: '',
          // One they did not. Setting a field to undefined is a no-op in
          // Mongoose, so writing `description: undefined` would leave the
          // deleted account's old text in place.
          ...(input.description === undefined ? { description: '' } : {}),
        },
      },
      { returnDocument: 'after' },
    ).exec();
    return restored ? dto(restored) : null;
  }

  async abortServiceAccountRestore(
    id: string,
    restoreOpId: string,
  ): Promise<void> {
    await Users.updateOne(
      { _id: id, restoreOpId },
      { $set: { isDeleted: true }, $unset: { restoreOpId: '' } },
    ).exec();
  }

  async finishServiceAccountRestore(
    id: string,
    restoreOpId: string,
  ): Promise<UserDto | null> {
    const finished = await Users.findOneAndUpdate(
      { _id: id, restoreOpId, isDeleted: false },
      { $set: { isDisabled: false }, $unset: { restoreOpId: '' } },
      { returnDocument: 'after' },
    ).exec();
    return finished ? dto(finished) : null;
  }

  async enableServiceAccount(orgId: string, id: string): Promise<boolean> {
    const enabled = await Users.findOneAndUpdate(
      {
        _id: id,
        orgId,
        kind: 'service',
        isDeleted: false,
        restoreOpId: { $exists: false },
      },
      { $set: { isDisabled: false } },
      { returnDocument: 'after' },
    ).exec();
    return !!enabled;
  }

  async updateServiceAccount(
    orgId: string,
    id: string,
    changes: Record<string, unknown>,
  ): Promise<boolean> {
    const written = await Users.updateOne(
      { _id: id, orgId, kind: 'service', isDeleted: false },
      { $set: changes },
    ).exec();
    return written.matchedCount !== 0;
  }
}
