import type { ClientSession } from 'mongoose';
import { Users, type UserRole } from '../schema/users.schema';
import { UserGroups } from '../schema/userGroup.schema';

/**
 * Data-access helpers for org-admin role checks.
 * Keeps Mongoose queries out of the service layer.
 */
export const UserAdminRepository = {
  async findActiveUserRole(
    userId: string,
    orgId: string,
  ): Promise<{ role?: UserRole | null } | null> {
    return Users.findOne({
      _id: userId,
      orgId,
      isDeleted: { $ne: true },
    })
      .select('role')
      .lean();
  },

  async findActiveGroupTypesForUser(
    userId: string,
    orgId: string,
  ): Promise<Array<{ type?: string }>> {
    return UserGroups.find({
      orgId,
      users: { $in: [userId] },
      isDeleted: { $ne: true },
    }).select('type');
  },

  async findActiveAdminUserIds(
    orgId: string | { toString(): string },
  ): Promise<Array<{ _id?: unknown }>> {
    return Users.find({
      orgId,
      role: 'admin',
      isDeleted: { $ne: true },
    })
      .select('_id')
      .lean();
  },

  async findActiveAdminGroupUsers(
    orgId: string | { toString(): string },
  ): Promise<Array<{ users?: unknown }>> {
    return UserGroups.find({
      orgId,
      type: 'admin',
      isDeleted: { $ne: true },
    })
      .select('users')
      .lean();
  },

  async countActiveAdmins(
    orgId: string,
    session?: ClientSession | null,
  ): Promise<number> {
    const query = Users.countDocuments({
      orgId,
      role: 'admin',
      isDeleted: { $ne: true },
    });
    return session ? query.session(session) : query;
  },

  async restoreAdminRole(
    userId: string,
    orgId: string,
    session?: ClientSession | null,
  ): Promise<void> {
    const filter = { _id: userId, orgId, isDeleted: { $ne: true } };
    const update = { $set: { role: 'admin' as const } };
    if (session) {
      await Users.updateOne(filter, update, { session });
      return;
    }
    await Users.updateOne(filter, update);
  },
};
