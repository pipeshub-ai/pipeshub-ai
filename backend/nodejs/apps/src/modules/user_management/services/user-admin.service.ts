import { BadRequestError } from '../../../libs/errors/http.errors';
import { isValidId } from '../../../libs/repositories/ids';
import {
  createUnitOfWork,
  unitsAreAtomic,
} from '../../../libs/repositories/doc-store';
import type { Tx } from '../../../libs/repositories/uow';
import { NotFoundError } from '../../../libs/errors/http.errors';
import {
  getUserRepository,
  type UserDto,
  type UserFields,
  type UserRole,
} from '../../../libs/repositories/user';

const LAST_ADMIN_DEMOTION_MESSAGE =
  'Cannot demote the last admin. Promote another user to admin first.';

export const MAX_ORG_ADMINS = 5;

export const ADMIN_ACCESS_REQUIRED_MESSAGE =
  'You need admin access to do this. Ask an admin in your organisation.';

export const OWN_ADMIN_CHECK_ONLY_MESSAGE =
  'You can only check your own admin access.';

export const MAX_ORG_ADMINS_MESSAGE =
  'An organization can have at most 5 admins.';

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
  if (isValidId(asString)) {
    ids.add(asString);
  }
}

/**
 * Live role of an active (not deleted) user in the org, or null when there is none.
 * Based on User.role (admin groups are no longer supported). Invalid ids return null
 * instead of throwing CastError.
 */
export const getActiveUserOrgRole = async (
  userId: string,
  orgId: string,
): Promise<UserRole | null> => {
  if (!isValidId(userId) || !isValidId(orgId)) {
    return null;
  }
  const user = await getUserRepository().getRoleSnapshot(orgId, userId);
  if (!user) {
    return null;
  }
  // A service account is a member whatever its record says.
  //
  // The schema refuses it the admin role on every write path, so today this
  // cannot differ. It is here because the two sibling queries in the
  // repository already exclude service accounts and this one could not, and
  // three queries in one file disagreeing about the same rule is what gets
  // lost in a refactor. It also matches the auth middleware, which clamps the
  // role on a token rather than trusting the record.
  //
  // Clamped rather than refused: this answers "what is this caller", and the
  // caller may legitimately be a service account.
  if (user.kind === 'service' || user.kind === 'slack') {
    return 'member';
  }
  return user.role === 'admin' ? 'admin' : 'member';
};

/**
 * Org admin check based on User.role. Invalid ids return false (deny) instead of
 * throwing CastError.
 */
export const isUserOrgAdmin = async (
  userId: string,
  orgId: string,
): Promise<boolean> => (await getActiveUserOrgRole(userId, orgId)) === 'admin';

/**
 * Active org admin user IDs for notifications / internal APIs.
 * Uses User.role === 'admin' only (admin groups are no longer supported).
 */
export const findOrgAdminUserIds = async (
  orgId: string | { toString(): string },
): Promise<string[]> => {
  const ids = new Set<string>();

  for (const id of await getUserRepository().listAdminIds(String(orgId))) {
    addValidObjectId(ids, id);
  }

  return [...ids];
};

/**
 * Pre-update check: reject demoting when this would remove the org's last admin.
 */
export const assertCanDemoteAdmin = async (
  orgId: string,
  tx?: Tx,
): Promise<void> => {
  const adminCount = await getUserRepository().countActiveAdmins(orgId, tx);
  if (adminCount <= 1) {
    throw new BadRequestError(LAST_ADMIN_DEMOTION_MESSAGE);
  }
};

/**
 * Pre-write check: reject promoting when this would take the org past MAX_ORG_ADMINS.
 * `additionalAdmins` is how many users would newly become admins (not already counted).
 */
export const assertCanPromoteAdmin = async (
  orgId: string,
  additionalAdmins: number = 1,
  tx?: Tx,
): Promise<void> => {
  if (additionalAdmins <= 0) {
    return;
  }
  const adminCount = await getUserRepository().countActiveAdmins(orgId, tx);
  if (adminCount + additionalAdmins > MAX_ORG_ADMINS) {
    throw new BadRequestError(MAX_ORG_ADMINS_MESSAGE);
  }
};

/**
 * Persist a user update that may demote an admin.
 * - Replica set: lock the org's admin changes + check + save in one txn.
 * - Non-RS: check, save, then verify; restore admin if the org was left with zero.
 */
export const saveUserEnsuringOrgRetainsAdmin = async (
  orgId: string,
  userId: string,
  fields: UserFields,
  rsAvailable: boolean,
): Promise<UserDto> => {
  const users = getUserRepository();
  let saved: UserDto | null = null;

  if (!unitsAreAtomic(rsAvailable)) {
    await assertCanDemoteAdmin(orgId);
    saved = await users.updateProfile(orgId, userId, fields);
    const adminCount = await users.countActiveAdmins(orgId);
    if (adminCount === 0) {
      await users.setRole(orgId, userId, 'admin');
      throw new BadRequestError(LAST_ADMIN_DEMOTION_MESSAGE);
    }
  } else {
    await createUnitOfWork(true).run(async (tx) => {
      await users.lockOrgAdminChanges(orgId, tx);
      await assertCanDemoteAdmin(orgId, tx);
      saved = await users.updateProfile(orgId, userId, fields, tx);
    });
  }
  if (!saved) throw new NotFoundError('User not found');
  return saved;
};

/**
 * Persist a user update that may promote a member to admin.
 * Same replica-set vs non-RS pattern as last-admin demotion.
 */
export const saveUserEnsuringAdminCap = async (
  orgId: string,
  userId: string,
  fields: UserFields,
  rsAvailable: boolean,
): Promise<UserDto> => {
  const users = getUserRepository();
  let saved: UserDto | null = null;

  if (!unitsAreAtomic(rsAvailable)) {
    await assertCanPromoteAdmin(orgId);
    saved = await users.updateProfile(orgId, userId, fields);
    const adminCount = await users.countActiveAdmins(orgId);
    if (adminCount > MAX_ORG_ADMINS) {
      await users.setRole(orgId, userId, 'member');
      throw new BadRequestError(MAX_ORG_ADMINS_MESSAGE);
    }
  } else {
    await createUnitOfWork(true).run(async (tx) => {
      await users.lockOrgAdminChanges(orgId, tx);
      await assertCanPromoteAdmin(orgId, 1, tx);
      saved = await users.updateProfile(orgId, userId, fields, tx);
    });
  }
  if (!saved) throw new NotFoundError('User not found');
  return saved;
};
