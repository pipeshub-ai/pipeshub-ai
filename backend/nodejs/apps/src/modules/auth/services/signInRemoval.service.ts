import { Types } from 'mongoose';
import { OrgAuthConfig } from '../schema/orgAuthConfiguration.schema';
import { UserCredentials } from '../schema/userCredentials.schema';
import { Users } from '../../user_management/schema/users.schema';

export interface PersonWithoutPassword {
  userId: string;
  fullName?: string;
  email: string;
}

export interface SignInRemovalNotice {
  /** Sign-in methods the 1.0 upgrade turned off for this workspace. */
  removedMethods: string[];
  /** People who have signed in but never set a password. */
  peopleWithoutPassword: PersonWithoutPassword[];
}

/**
 * What an admin needs after the upgrade turned sign-in methods off: which
 * ones, and who may now be unable to sign in because they have no password.
 */
export async function findSignInRemovalNotice(
  orgId: string,
): Promise<SignInRemovalNotice> {
  const policy = await OrgAuthConfig.findOne({ orgId, isDeleted: false })
    .select('removedSignInMethods')
    .lean<{ removedSignInMethods?: string[] }>();
  const removedMethods = policy?.removedSignInMethods ?? [];
  if (removedMethods.length === 0) {
    return { removedMethods, peopleWithoutPassword: [] };
  }

  // Only people who may sign in: not disabled accounts or service accounts.
  const people = await Users.find({
    orgId,
    isDeleted: { $ne: true },
    hasLoggedIn: true,
    isDisabled: { $ne: true },
    kind: { $ne: 'service' },
  })
    .select('_id fullName email')
    .lean<{ _id: Types.ObjectId; fullName?: string; email: string }[]>();
  const withPassword = await UserCredentials.find({
    orgId,
    isDeleted: { $ne: true },
    userId: { $in: people.map((p) => p._id.toHexString()) },
    hashedPassword: { $exists: true, $nin: [null, ''] },
  })
    .select('userId')
    .lean<{ userId: string }[]>();
  const hasPassword = new Set(withPassword.map((c) => c.userId));

  return {
    removedMethods,
    peopleWithoutPassword: people
      .filter((p) => !hasPassword.has(p._id.toHexString()))
      .map((p) => ({
        userId: p._id.toHexString(),
        ...(p.fullName ? { fullName: p.fullName } : {}),
        email: p.email,
      })),
  };
}
