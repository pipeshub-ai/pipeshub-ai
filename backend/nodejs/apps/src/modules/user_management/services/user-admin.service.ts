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
 * Org admin check: prefers User.role after migration; falls back to legacy
 * admin UserGroup membership when role is not yet set.
 */
export const isUserOrgAdmin = async (
  userId: string,
  orgId: string,
): Promise<boolean> => {
  const user = await UserAdminRepository.findActiveUserRole(userId, orgId);

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
  const groups = await UserAdminRepository.findActiveGroupTypesForUser(
    userId,
    orgId,
  );

  return groups.some(
    (userGroup) =>
      userGroup != null &&
      typeof userGroup === 'object' &&
      userGroup.type === 'admin',
  );
};

/**
 * Active org admin user IDs for notifications / internal APIs.
 * Prefers User.role === 'admin', and unions members of any still-active
 * legacy type=admin UserGroup (partial migration / retry safety).
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

  const adminGroups = await UserAdminRepository.findActiveAdminGroupUsers(orgId);
  for (const group of adminGroups) {
    if (group == null || typeof group !== 'object') continue;
    if (!Array.isArray(group.users)) continue;
    for (const userId of group.users) {
      addValidObjectId(ids, userId);
    }
  }

  return [...ids];
};

/**
 * After an admin→member write, ensure the org still has an admin.
 * If concurrent demotions left zero admins, restore this user and fail.
 * Prefer {@link saveUserEnsuringOrgRetainsAdmin} when a replica set is available.
 */
export const ensureOrgRetainsAdminAfterDemotion = async (
  userId: string,
  orgId: string,
  session?: ClientSession | null,
): Promise<void> => {
  const adminCount = await UserAdminRepository.countActiveAdmins(orgId, session);

  if (adminCount > 0) {
    return;
  }

  await UserAdminRepository.restoreAdminRole(userId, orgId, session);
  throw new BadRequestError(LAST_ADMIN_DEMOTION_MESSAGE);
};

/**
 * Persist a user update that may demote an admin.
 * With a replica set: save + admin-count check in one transaction (rollback on zero admins).
 * Without: save then restore+reject if zero admins remain.
 */
export const saveUserEnsuringOrgRetainsAdmin = async (
  user: User,
  rsAvailable: boolean,
): Promise<void> => {
  const userId = String(user._id);
  const orgId = String(user.orgId);

  if (!rsAvailable) {
    await user.save();
    await ensureOrgRetainsAdminAfterDemotion(userId, orgId);
    return;
  }

  const session = await mongoose.startSession();
  try {
    await session.withTransaction(async () => {
      await user.save({ session });
      const adminCount = await UserAdminRepository.countActiveAdmins(
        orgId,
        session,
      );
      if (adminCount === 0) {
        throw new BadRequestError(LAST_ADMIN_DEMOTION_MESSAGE);
      }
    });
  } finally {
    await session.endSession();
  }
};
