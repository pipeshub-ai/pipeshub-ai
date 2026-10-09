import type { ClientSession } from 'mongoose';
import { Users, type UserRole } from '../schema/users.schema';
import { Org } from '../schema/org.schema';
import { UserCredentials } from '../../auth/schema/userCredentials.schema';

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
      ...NOT_A_SERVICE_ACCOUNT,
    })
      .select('_id')
      .lean();
  },

  async findActiveAdmins(
    orgId: string,
  ): Promise<Array<{ _id: unknown; email?: string; createdAt?: Date }>> {
    return Users.find({
      orgId,
      role: 'admin',
      isDeleted: { $ne: true },
      ...NOT_A_SERVICE_ACCOUNT,
    })
      .select('_id email createdAt')
      .lean();
  },

  async findOrgContactEmail(orgId: string): Promise<string | null> {
    const org = await Org.findOne({ _id: orgId, isDeleted: { $ne: true } })
      .select('contactEmail')
      .lean();
    return org?.contactEmail ?? null;
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

  async isLoginBlocked(userId: string, orgId: string): Promise<boolean> {
    const blocked = await UserCredentials.exists({
      userId,
      orgId,
      isBlocked: true,
      isDeleted: { $ne: true },
    });
    return blocked !== null;
  },

  /**
   * Admin handover: makes a signed-in, enabled member an admin. Returns null
   * when the user is not such a member, so the caller can abort the handover.
   */
  async promoteSignedInMember(
    userId: string,
    orgId: string,
    session?: ClientSession | null,
  ): Promise<{ _id: unknown; email?: string } | null> {
    return Users.findOneAndUpdate(
      {
        _id: userId,
        orgId,
        role: { $ne: 'admin' },
        hasLoggedIn: true,
        isDisabled: { $ne: true },
        isDeleted: { $ne: true },
        ...NOT_A_SERVICE_ACCOUNT,
      },
      { $set: { role: 'admin' as const } },
      { session: session ?? undefined, projection: { _id: 1, email: 1 } },
    ).lean();
  },

  /** Admin handover: makes the outgoing admin a member, or returns null if they are not one. */
  async demoteAdmin(
    userId: string,
    orgId: string,
    session?: ClientSession | null,
  ): Promise<{ _id: unknown; email?: string } | null> {
    return Users.findOneAndUpdate(
      { _id: userId, orgId, role: 'admin', isDeleted: { $ne: true } },
      { $set: { role: 'member' as const } },
      { session: session ?? undefined, projection: { _id: 1, email: 1 } },
    ).lean();
  },
};
