import { Users, type UserRole } from '../schema/users.schema';
import { UserGroups, type UserGroup } from '../schema/userGroup.schema';
import { BadRequestError } from '../../../libs/errors/http.errors';

const LAST_ADMIN_DEMOTION_MESSAGE =
  'Cannot demote the last admin. Promote another user to admin first.';

/** Normalize API/UI role labels to the stored enum. */
export function normalizeUserRole(role: string | undefined | null): UserRole | null {
  if (!role) return null;
  const normalized = role.trim().toLowerCase();
  if (normalized === 'admin') return 'admin';
  if (normalized === 'member') return 'member';
  return null;
}

/**
 * Optional role for create/invite: absent → member; present but invalid → error.
 */
export function resolveOptionalUserRole(
  role: string | undefined | null,
): UserRole {
  if (role === undefined || role === null || String(role).trim() === '') {
    return 'member';
  }
  const normalized = normalizeUserRole(String(role));
  if (!normalized) {
    throw new BadRequestError('Invalid role. Must be admin or member');
  }
  return normalized;
}

/** API/UI display label for a stored role. */
export function toDisplayUserRole(role: string | undefined | null): 'Admin' | 'Member' {
  return normalizeUserRole(role) === 'admin' ? 'Admin' : 'Member';
}

/**
 * Org admin check: prefers User.role after migration; falls back to legacy
 * admin UserGroup membership when role is not yet set.
 */
export const isUserOrgAdmin = async (
  userId: string,
  orgId: string,
): Promise<boolean> => {
  const user = await Users.findOne({
    _id: userId,
    orgId,
    isDeleted: { $ne: true },
  })
    .select('role')
    .lean();

  if (user?.role === 'admin') {
    return true;
  }
  if (user?.role === 'member') {
    return false;
  }

  // Pre-migration fallback: membership in type=admin group
  const groups = await UserGroups.find({
    orgId,
    users: { $in: [userId] },
    isDeleted: { $ne: true },
  }).select('type');

  return groups.some((userGroup: UserGroup) => userGroup.type === 'admin');
};

/**
 * Fast pre-check: blocks demoting an org admin when no other User.role=admin
 * remains. Not sufficient alone under concurrency — pair with
 * {@link ensureOrgRetainsAdminAfterDemotion} after the role write.
 */
export const assertNotLastOrgAdminDemotion = async (
  userId: string,
  orgId: string,
): Promise<void> => {
  const isAdmin = await isUserOrgAdmin(userId, orgId);
  if (!isAdmin) {
    return;
  }

  const otherAdmins = await Users.countDocuments({
    orgId,
    _id: { $ne: userId },
    role: 'admin',
    isDeleted: { $ne: true },
  });

  if (otherAdmins === 0) {
    throw new BadRequestError(LAST_ADMIN_DEMOTION_MESSAGE);
  }
};

/**
 * Post-write guard against concurrent last-admin demotions.
 * If the org has zero admins after a demotion, restore this user to admin and fail.
 */
export const ensureOrgRetainsAdminAfterDemotion = async (
  userId: string,
  orgId: string,
): Promise<void> => {
  const adminCount = await Users.countDocuments({
    orgId,
    role: 'admin',
    isDeleted: { $ne: true },
  });

  if (adminCount > 0) {
    return;
  }

  await Users.updateOne(
    { _id: userId, orgId, isDeleted: { $ne: true } },
    { $set: { role: 'admin' } },
  );

  throw new BadRequestError(LAST_ADMIN_DEMOTION_MESSAGE);
};
