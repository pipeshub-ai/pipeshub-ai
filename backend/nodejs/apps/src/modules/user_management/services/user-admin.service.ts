import { BadRequestError, ForbiddenError } from '../../../libs/errors/http.errors';
import mongoose, { type ClientSession } from 'mongoose';
import { type User, type UserRole } from '../schema/users.schema';
import { UserAdminRepository } from '../repositories/user-admin.repository';

const LAST_ADMIN_DEMOTION_MESSAGE =
  'Cannot demote the last admin. Promote another user to admin first.';

export const MAX_ORG_ADMINS = 1;

// null means no cap. The edition sets this at boot from config.ts: Community
// Edition keeps MAX_ORG_ADMINS, Enterprise Edition has no admin limit.
let orgAdminLimit: number | null = MAX_ORG_ADMINS;

export function setOrgAdminLimit(limit: number | null): void {
  orgAdminLimit = limit;
}

export function getOrgAdminLimit(): number | null {
  return orgAdminLimit;
}

export const ADMIN_ACCESS_REQUIRED_MESSAGE =
  'You need admin access to do this. Ask an admin in your organisation.';

export const OWN_ADMIN_CHECK_ONLY_MESSAGE =
  'You can only check your own admin access.';

export const MAX_ORG_ADMINS_MESSAGE =
  'An organization can have at most 1 admin.';

export const ADMIN_TRANSFER_SELF_MESSAGE = 'You are already an admin.';

export const ADMIN_TRANSFER_TARGET_MESSAGE =
  'The admin role can only be handed to an active member who has signed in.';

// Orgs that already had more admins than MAX_ORG_ADMINS keep them until this
// date; CommunityAdminLimitMigration then makes every admin but one a member.
export const ADMIN_LIMIT_ENFORCEMENT_DATE = new Date('2027-01-01T00:00:00.000Z');

export interface AdminCandidate {
  _id: unknown;
  email?: string | null;
  createdAt?: Date | string | null;
}

export interface OrgAdminLimitStatus {
  adminCount: number;
  maxAdmins: number | null;
  overLimit: boolean;
  enforcementDate: string;
  retainedAdminEmail: string | null;
}

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
 * Live role of an active (not deleted) user in the org, or null when there is none.
 * Based on User.role (admin groups are no longer supported). Invalid ids return null
 * instead of throwing CastError.
 */
export const getActiveUserOrgRole = async (
  userId: string,
  orgId: string,
): Promise<UserRole | null> => {
  if (!mongoose.isValidObjectId(userId) || !mongoose.isValidObjectId(orgId)) {
    return null;
  }
  const user = await UserAdminRepository.findActiveUserRole(userId, orgId);
  if (!user) {
    return null;
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
 * Pre-write check: reject promoting when this would take the org past MAX_ORG_ADMINS.
 * `additionalAdmins` is how many users would newly become admins (not already counted).
 */
export const assertCanPromoteAdmin = async (
  orgId: string,
  additionalAdmins: number = 1,
  session?: ClientSession | null,
): Promise<void> => {
  const limit = orgAdminLimit;
  if (additionalAdmins <= 0 || limit === null) {
    return;
  }
  const adminCount = await UserAdminRepository.countActiveAdmins(orgId, session);
  if (adminCount + additionalAdmins > limit) {
    throw new BadRequestError(MAX_ORG_ADMINS_MESSAGE);
  }
};

/**
 * Post-write check for a write that may have added an admin: two concurrent
 * writes can each pass assertCanPromoteAdmin and together exceed the limit.
 */
export const isOrgOverAdminLimit = async (orgId: string): Promise<boolean> => {
  const limit = orgAdminLimit;
  if (limit === null) return false;
  return (await UserAdminRepository.countActiveAdmins(orgId)) > limit;
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

/**
 * Persist a user update that may promote a member to admin.
 * Same replica-set vs non-RS pattern as last-admin demotion.
 */
export const saveUserEnsuringAdminCap = async (
  user: User,
  rsAvailable: boolean,
): Promise<void> => {
  const limit = orgAdminLimit;
  if (limit === null) {
    await user.save();
    return;
  }
  const orgId = String(user.orgId);
  const userId = String(user._id);

  if (!rsAvailable) {
    await assertCanPromoteAdmin(orgId);
    await user.save();
    const adminCount = await UserAdminRepository.countActiveAdmins(orgId);
    if (adminCount > limit) {
      await UserAdminRepository.restoreMemberRole(userId, orgId);
      user.role = 'member';
      throw new BadRequestError(MAX_ORG_ADMINS_MESSAGE);
    }
    return;
  }

  const session = await mongoose.startSession();
  try {
    await session.withTransaction(async () => {
      await UserAdminRepository.touchOrgAdminGuard(orgId, session);
      await assertCanPromoteAdmin(orgId, 1, session);
      await user.save({ session });
    });
  } finally {
    await session.endSession();
  }
};

function createdAtMillis(admin: AdminCandidate): number {
  const time = admin.createdAt ? new Date(admin.createdAt).getTime() : NaN;
  return Number.isFinite(time) ? time : Number.POSITIVE_INFINITY;
}

/**
 * The admin who stays admin when an org is brought down to MAX_ORG_ADMINS: the
 * org's contact (the person who set it up), else the longest-standing admin.
 * Deterministic, so the notice and the migration name the same person.
 */
export function selectAdminToRetain<T extends AdminCandidate>(
  admins: T[],
  contactEmail?: string | null,
): T | null {
  if (admins.length === 0) return null;
  const contact = contactEmail?.trim().toLowerCase();
  if (contact) {
    const owner = admins.find((a) => a.email?.trim().toLowerCase() === contact);
    if (owner) return owner;
  }
  return [...admins].sort(
    (a, b) =>
      createdAtMillis(a) - createdAtMillis(b) ||
      String(a._id).localeCompare(String(b._id)),
  )[0]!;
}

export const getOrgAdminLimitStatus = async (
  orgId: string,
): Promise<OrgAdminLimitStatus> => {
  const limit = orgAdminLimit;
  const admins = await UserAdminRepository.findActiveAdmins(orgId);
  const overLimit = limit !== null && admins.length > limit;
  let retainedAdminEmail: string | null = null;
  if (overLimit) {
    const contactEmail = await UserAdminRepository.findOrgContactEmail(orgId);
    retainedAdminEmail = selectAdminToRetain(admins, contactEmail)?.email ?? null;
  }
  return {
    adminCount: admins.length,
    maxAdmins: limit,
    overLimit,
    enforcementDate: ADMIN_LIMIT_ENFORCEMENT_DATE.toISOString(),
    retainedAdminEmail,
  };
};

export interface AdminTransferParty {
  userId: string;
  email?: string;
}

export interface AdminTransferResult {
  previousAdmin: AdminTransferParty;
  newAdmin: AdminTransferParty;
}

function toTransferParty(doc: { _id: unknown; email?: string }): AdminTransferParty {
  return { userId: String(doc._id), email: doc.email };
}

/**
 * Hands the caller's admin role to a member: the member becomes admin and the
 * caller becomes a member, so the org's admin count does not change and the
 * admin limit never blocks it. The target must have signed in and not be
 * blocked, or the org could end up with an admin nobody can sign in as.
 */
export const transferOrgAdmin = async (
  orgId: string,
  fromUserId: string,
  toUserId: string,
  rsAvailable: boolean,
): Promise<AdminTransferResult> => {
  if (fromUserId === toUserId) {
    throw new BadRequestError(ADMIN_TRANSFER_SELF_MESSAGE);
  }
  if (await UserAdminRepository.isLoginBlocked(toUserId, orgId)) {
    throw new BadRequestError(ADMIN_TRANSFER_TARGET_MESSAGE);
  }

  if (!rsAvailable) {
    // Promote first: a failure in between leaves two admins, never none.
    const newAdmin = await UserAdminRepository.promoteSignedInMember(toUserId, orgId);
    if (!newAdmin) {
      throw new BadRequestError(ADMIN_TRANSFER_TARGET_MESSAGE);
    }
    const previousAdmin = await UserAdminRepository.demoteAdmin(fromUserId, orgId);
    if (!previousAdmin) {
      await UserAdminRepository.restoreMemberRole(toUserId, orgId);
      throw new ForbiddenError(ADMIN_ACCESS_REQUIRED_MESSAGE);
    }
    return {
      previousAdmin: toTransferParty(previousAdmin),
      newAdmin: toTransferParty(newAdmin),
    };
  }

  const session = await mongoose.startSession();
  try {
    let result = null as AdminTransferResult | null;
    await session.withTransaction(async () => {
      await UserAdminRepository.touchOrgAdminGuard(orgId, session);
      const newAdmin = await UserAdminRepository.promoteSignedInMember(toUserId, orgId, session);
      if (!newAdmin) {
        throw new BadRequestError(ADMIN_TRANSFER_TARGET_MESSAGE);
      }
      const previousAdmin = await UserAdminRepository.demoteAdmin(fromUserId, orgId, session);
      if (!previousAdmin) {
        throw new ForbiddenError(ADMIN_ACCESS_REQUIRED_MESSAGE);
      }
      result = {
        previousAdmin: toTransferParty(previousAdmin),
        newAdmin: toTransferParty(newAdmin),
      };
    });
    if (!result) {
      throw new BadRequestError(ADMIN_TRANSFER_TARGET_MESSAGE);
    }
    return result;
  } finally {
    await session.endSession();
  }
};
