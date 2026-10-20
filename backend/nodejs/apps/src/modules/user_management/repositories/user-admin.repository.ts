import type { ClientSession } from 'mongoose';
import { Users, type UserRole } from '../schema/users.schema';
import { Org } from '../schema/org.schema';

/**
 * Data-access helpers for org-admin role checks.
 * Keeps Mongoose queries out of the service layer.
 */
/**
 * These queries answer "who administers this organisation", and both callers
 * mean people by it: one notifies administrators, the other refuses to demote
 * the last one. A service account cannot hold the admin role — the schema
 * refuses it — but a row written straight to the database could, and counting
 * it would let the last person who can sign in be demoted, leaving an
 * organisation administered only by something nobody can log in as.
 *
 * `$ne` rather than a match on 'human', because records created before `kind`
 * existed have no value there at all.
 */
const NOT_A_SERVICE_ACCOUNT = { kind: { $ne: 'service' } } as const;

export const UserAdminRepository = {
  /**
   * Reads `kind` alongside the role, which the two queries below instead use
   * to filter.
   *
   * The difference is deliberate. Those answer "who administers this
   * organisation", so a service account should not be in the answer at all.
   * This one answers "what is this caller", and the caller may legitimately
   * be a service account — `GET /api/v1/users/me/role` is how the Python
   * services learn the role of whoever is asking, and filtering here would
   * return nothing and sign them out. So the kind comes back and the service
   * layer clamps it.
   */
  async findActiveUserRole(
    userId: string,
    orgId: string,
  ): Promise<{ role?: UserRole | null; kind?: string | null } | null> {
    return Users.findOne({
      _id: userId,
      orgId,
      isDeleted: { $ne: true },
    })
      .select('role kind')
      .lean();
  },

  async findActiveAdminUserIds(
    orgId: string | { toString(): string },
  ): Promise<Array<{ _id?: unknown }>> {
    return Users.find({
      orgId,
      role: 'admin',
      isDeleted: { $ne: true },
      ...NOT_A_SERVICE_ACCOUNT,
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
      ...NOT_A_SERVICE_ACCOUNT,
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

  async restoreMemberRole(
    userId: string,
    orgId: string,
    session?: ClientSession | null,
  ): Promise<void> {
    const filter = { _id: userId, orgId, isDeleted: { $ne: true } };
    const update = { $set: { role: 'member' as const } };
    if (session) {
      await Users.updateOne(filter, update, { session });
      return;
    }
    await Users.updateOne(filter, update);
  },
};
