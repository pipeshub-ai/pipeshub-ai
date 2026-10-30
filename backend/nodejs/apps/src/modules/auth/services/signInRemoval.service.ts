import { getCredentialRepository } from '../../../libs/repositories/credential';
import { getOrgAuthConfigRepository } from '../../../libs/repositories/org-auth-config';
import { getUserRepository } from '../../../libs/repositories/user';

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
  const removedMethods =
    await getOrgAuthConfigRepository().removedSignInMethods(orgId);
  if (removedMethods.length === 0) {
    return { removedMethods, peopleWithoutPassword: [] };
  }

  // Only people who may sign in: not disabled accounts or service accounts.
  const people = await getUserRepository().listSignInEligible(orgId);
  const hasPassword = new Set(
    await getCredentialRepository().userIdsWithPassword(
      orgId,
      people.map((p) => p._id),
    ),
  );

  return {
    removedMethods,
    peopleWithoutPassword: people
      .filter((p) => !hasPassword.has(p._id))
      .map((p) => ({
        userId: p._id,
        ...(p.fullName ? { fullName: p.fullName } : {}),
        email: p.email,
      })),
  };
}
