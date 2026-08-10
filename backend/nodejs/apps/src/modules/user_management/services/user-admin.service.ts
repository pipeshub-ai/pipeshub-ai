import { Users, type UserRole } from '../schema/users.schema';
import { UserGroups, type UserGroup } from '../schema/userGroup.schema';
import { BadRequestError } from '../../../libs/errors/http.errors';
import mongoose from 'mongoose';

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

  // Missing/deleted users must not inherit admin via stale group membership
  if (!user) {
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
 * Active org admin user IDs for notifications / internal APIs.
 * Prefers User.role === 'admin', and unions members of any still-active
 * legacy type=admin UserGroup (partial migration / retry safety).
 * This codebase has no Users repository layer — Mongoose models are the data access.
 */
export const findOrgAdminUserIds = async (
  orgId: string | { toString(): string },
): Promise<string[]> => {
  const ids = new Set<string>();

  const adminUsers = await Users.find({
    orgId,
    role: 'admin',
    isDeleted: { $ne: true },
  })
    .select('_id')
    .lean();

  for (const user of adminUsers) {
    ids.add(String(user._id));
  }

  const adminGroups = await UserGroups.find({
    orgId,
    type: 'admin',
    isDeleted: { $ne: true },
  })
    .select('users')
    .lean();

  for (const group of adminGroups) {
    for (const userId of (group as { users?: unknown[] }).users ?? []) {
      const asString = String(userId);
      if (mongoose.isValidObjectId(asString)) {
        ids.add(asString);
      }
    }
  }

  return [...ids];
};

/**
 * After an admin→member write, ensure the org still has an admin.
 * If concurrent demotions left zero admins, restore this user and fail.
 * Do not use a read-only count before the role write — that races.
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
