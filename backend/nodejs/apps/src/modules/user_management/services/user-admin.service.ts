import { BadRequestError } from '../../../libs/errors/http.errors';
import mongoose, { type ClientSession } from 'mongoose';
import { type User, type UserRole } from '../schema/users.schema';
import { UserAdminRepository } from '../repositories/user-admin.repository';

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

function addValidObjectId(ids: Set<string>, value: unknown): void {
  if (value == null) return;
  const asString = String(value);
  if (mongoose.isValidObjectId(asString)) {
    ids.add(asString);
  }
}

/**
 * Org admin check based on User.role (admin groups are no longer supported).
 * Invalid ids return false (deny) instead of throwing CastError.
 */
export const isUserOrgAdmin = async (
  userId: string,
  orgId: string,
): Promise<boolean> => {
  if (!mongoose.isValidObjectId(userId) || !mongoose.isValidObjectId(orgId)) {
    return false;
  }
  const user = await UserAdminRepository.findActiveUserRole(userId, orgId);
  return user?.role === 'admin';
};

/**
 * Active org admin user IDs for notifications / internal APIs.
 * Uses User.role === 'admin' only (admin groups are no longer supported).
 */
export const findOrgAdminUserIds = async (
  orgId: string | { toString(): string },
): Promise<string[]> => {
  const ids = new Set<string>();

  const adminUsers = await UserAdminRepository.findActiveAdminUserIds(orgId);
  for (const user of adminUsers) {
    if (user == null || typeof user !== 'object') continue;
    addValidObjectId(ids, user._id);
  }

  return [...ids];
};

/**
 * Pre-update check: reject demoting when this would remove the org's last admin.
 */
export const assertCanDemoteAdmin = async (
  orgId: string,
  session?: ClientSession | null,
): Promise<void> => {
  const adminCount = await UserAdminRepository.countActiveAdmins(orgId, session);
  if (adminCount <= 1) {
    throw new BadRequestError(LAST_ADMIN_DEMOTION_MESSAGE);
  }
};

/**
 * Persist a user update that may demote an admin.
 * - Replica set: touch Org (serialize concurrent demotions) + check + save in one txn.
 * - Non-RS: check, save, then verify; restore admin if the org was left with zero.
 */
export const saveUserEnsuringOrgRetainsAdmin = async (
  user: User,
  rsAvailable: boolean,
): Promise<void> => {
  const orgId = String(user.orgId);
  const userId = String(user._id);

  if (!rsAvailable) {
    await assertCanDemoteAdmin(orgId);
    await user.save();
    const adminCount = await UserAdminRepository.countActiveAdmins(orgId);
    if (adminCount === 0) {
      await UserAdminRepository.restoreAdminRole(userId, orgId);
      user.role = 'admin';
      throw new BadRequestError(LAST_ADMIN_DEMOTION_MESSAGE);
    }
    return;
  }

  const session = await mongoose.startSession();
  try {
    await session.withTransaction(async () => {
      await UserAdminRepository.touchOrgAdminGuard(orgId, session);
      await assertCanDemoteAdmin(orgId, session);
      await user.save({ session });
    });
  } finally {
    await session.endSession();
  }
};
