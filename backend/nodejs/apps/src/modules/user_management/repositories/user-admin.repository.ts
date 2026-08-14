import type { ClientSession } from 'mongoose';
import { Users, type UserRole } from '../schema/users.schema';
import { Org } from '../schema/org.schema';

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

  /**
   * Serializes concurrent last-admin demotions under snapshot isolation by
   * forcing a write conflict on the shared Org document inside the transaction.
   */
  async touchOrgAdminGuard(
    orgId: string,
    session: ClientSession,
  ): Promise<void> {
    await Org.updateOne(
      { _id: orgId, isDeleted: { $ne: true } },
      { $set: { adminRoleGuardAt: new Date() } },
      { session },
    );
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
