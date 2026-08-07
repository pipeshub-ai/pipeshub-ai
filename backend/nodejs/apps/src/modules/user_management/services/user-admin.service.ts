import { Users, type UserRole } from '../schema/users.schema';
import { UserGroups, type UserGroup } from '../schema/userGroup.schema';

/** Normalize API/UI role labels to the stored enum. */
export function normalizeUserRole(role: string | undefined | null): UserRole | null {
  if (!role) return null;
  const normalized = role.trim().toLowerCase();
  if (normalized === 'admin') return 'admin';
  if (normalized === 'member') return 'member';
  return null;
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
    isDeleted: false,
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
    isDeleted: false,
  }).select('type');

  return groups.some((userGroup: UserGroup) => userGroup.type === 'admin');
};
