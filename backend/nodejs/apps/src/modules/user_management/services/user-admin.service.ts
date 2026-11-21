import {
  BadRequestError,
  ServiceUnavailableError,
} from '../../../libs/errors/http.errors';
import { Logger } from '../../../libs/services/logger.service';
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
import { userRefusal } from '../utils/user-audit';

const LAST_ADMIN_DEMOTION_MESSAGE =
  'Cannot demote the last admin. Promote another user to admin first.';

export const MAX_ORG_ADMINS = 5;

const ADMIN_LEASE_LOST_MESSAGE =
  'This administrator change took too long and was not made. Try again.';

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
  if (user.kind === 'service') {
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
    throw userRefusal(
      new BadRequestError(LAST_ADMIN_DEMOTION_MESSAGE),
      'last_admin',
    );
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
    throw userRefusal(
      new BadRequestError(MAX_ORG_ADMINS_MESSAGE),
      'admin_limit',
    );
  }
};

/**
 * Persist a user update that may demote an admin.
 * - Replica set: lock the org's admin changes + check + save in one txn.
 * - Non-RS: under the org's admin-role lease, check, confirm the lease, save,
 *   then verify; restore admin if the org was left with zero. A demotion that
 *   outlived its lease is not made: its check may have counted another change's
 *   provisional admin. Restoring adds an admin, so it never needs the lease.
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
    saved = await users.withAdminLease(orgId, async (lease) => {
      await assertCanDemoteAdmin(orgId);
      if (!(await lease.confirm())) {
        throw new ServiceUnavailableError(ADMIN_LEASE_LOST_MESSAGE);
      }
      const demoted = await users.updateProfile(orgId, userId, fields);
      const adminCount = await users.countActiveAdmins(orgId);
      if (adminCount === 0) {
        await users.setRole(orgId, userId, 'admin');
        throw userRefusal(
          new BadRequestError(LAST_ADMIN_DEMOTION_MESSAGE),
          'last_admin',
        );
      }
      return demoted;
    });
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
 * Run a write that makes users admins without taking the org past MAX_ORG_ADMINS.
 * - Replica set / PostgreSQL: lock the org's admin changes, write, then count inside
 *   the same transaction; over the cap the transaction rolls back. Counting after
 *   the write makes the check exact however many admins the write grants.
 * - Non-RS: under the org's admin-role lease, check, write, re-count, and `undo`
 *   the write if the org went over or the re-count failed. Without the lease, a
 *   demotion could pass because this write's admin exists, and the undo would then
 *   leave the org with none. So the undo runs only once the lease is confirmed; a
 *   write that outlived its lease stays (possibly over the cap, which an admin can
 *   fix; an org with no admin cannot be fixed from the app).
 * The non-RS pre-check assumes one new admin, so a write that grants none must
 * not come through here.
 */
export const writeEnsuringAdminCap = async <T>(
  orgId: string,
  rsAvailable: boolean,
  write: (tx?: Tx) => Promise<T>,
  undo: (saved: T) => Promise<void>,
): Promise<T> => {
  const users = getUserRepository();
  const overTheCap = (): BadRequestError =>
    userRefusal(new BadRequestError(MAX_ORG_ADMINS_MESSAGE), 'admin_limit');

  if (!unitsAreAtomic(rsAvailable)) {
    return users.withAdminLease(orgId, async (lease) => {
      await assertCanPromoteAdmin(orgId);
      const saved = await write();
      /** False when the lease was lost: another change may count this admin, so it stays. */
      const undone = async (): Promise<boolean> => {
        if (await lease.confirm()) {
          await undo(saved);
          return true;
        }
        Logger.getInstance().warn(
          'Admin-role lease lost before an admin grant could be taken back; the grant stays, possibly over the admin limit',
          { orgId },
        );
        return false;
      };
      let adminCount: number;
      try {
        adminCount = await users.countActiveAdmins(orgId);
      } catch (error) {
        // Unchecked against the cap, the write must not stay.
        if (await undone()) throw error;
        return saved;
      }
      if (adminCount > MAX_ORG_ADMINS && (await undone())) {
        throw overTheCap();
      }
      return saved;
    });
  }
  return createUnitOfWork(true).run(async (tx) => {
    await users.lockOrgAdminChanges(orgId, tx);
    const saved = await write(tx);
    if ((await users.countActiveAdmins(orgId, tx)) > MAX_ORG_ADMINS) {
      throw overTheCap();
    }
    return saved;
  });
};

/** Persist a user update that may promote a member to admin. */
export const saveUserEnsuringAdminCap = async (
  orgId: string,
  userId: string,
  fields: UserFields,
  rsAvailable: boolean,
): Promise<UserDto> => {
  const users = getUserRepository();
  const saved = await writeEnsuringAdminCap(
    orgId,
    rsAvailable,
    (tx) => users.updateProfile(orgId, userId, fields, tx),
    () => users.setRole(orgId, userId, 'member'),
  );
  if (!saved) throw new NotFoundError('User not found');
  return saved;
};

/**
 * Create a user whose role is admin. A refused create leaves no account: the
 * non-RS undo is a hard delete, so the address can be used again.
 */
export const createUserEnsuringAdminCap = async (
  orgId: string,
  fields: UserFields,
  rsAvailable: boolean,
): Promise<UserDto> => {
  const users = getUserRepository();
  return writeEnsuringAdminCap(
    orgId,
    rsAvailable,
    (tx) => users.create(orgId, fields, tx),
    (saved) => users.deleteById(saved._id),
  );
};
