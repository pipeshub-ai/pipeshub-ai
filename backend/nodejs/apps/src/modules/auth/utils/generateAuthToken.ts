import {
  ForbiddenError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import {
  authJwtGenerator,
  fetchConfigJwtGenerator,
} from '../../../libs/utils/createJwt';
import { findActiveOrgById } from '../../user_management/utils/org.utils';
import { normalizeUserRole } from '../../user_management/services/user-admin.service';
import { Users } from '../../user_management/schema/users.schema';
import mongoose from 'mongoose';

export const SERVICE_ACCOUNT_SIGN_IN_MESSAGE =
  'Service accounts cannot sign in. Use a service token instead.';

export const DISABLED_ACCOUNT_SIGN_IN_MESSAGE =
  'This account is disabled. Ask an administrator to re-enable it.';

/**
 * Issues the session token that every interactive sign-in ends with.
 *
 * Password, OTP, Google, Microsoft, Azure AD, generic OAuth, SAML, the refresh
 * exchange and the OAuth token exchange all converge here, which makes this
 * the one place worth checking whether this account is allowed a session at
 * all. Two kinds are not: service accounts, which exist to be authenticated as
 * by machines and never by people, and accounts an administrator has disabled.
 *
 * The check reads `kind` and `isDisabled` from the database rather than from
 * the `user` object it was handed, because callers build that object in
 * different ways — one of them from an internal HTTP response whose field set
 * is decided elsewhere. A guard that can be defeated by an upstream projection
 * dropping a field is not a guard, and this one fails closed: if the record
 * cannot be found, no token is issued.
 *
 * Note that this deliberately does not touch OAuth access tokens or personal
 * access tokens. Those are minted by the OAuth provider, and in the next phase
 * a service account being able to hold one is the entire point.
 */
export async function generateAuthToken(
  user: Record<string, any>,
  jwtSecret: string,
) {
  // Same normalisation the org lookup does, and for the same reason: a
  // freshly provisioned user is a Mongoose document, so its _id is an
  // ObjectId rather than a string. Anything that is not a real id falls
  // through the guard and is refused without touching the database, rather
  // than surfacing as an unhandled cast error.
  const rawId: unknown = user._id;
  const userId =
    rawId instanceof mongoose.Types.ObjectId ? rawId.toHexString() : rawId;
  if (typeof userId !== 'string' || !mongoose.isValidObjectId(userId)) {
    throw new NotFoundError('User not found');
  }

  const account = await Users.findOne({ _id: userId, isDeleted: false })
    .select('kind isDisabled')
    .lean()
    .exec();

  if (!account) {
    throw new NotFoundError('User not found');
  }
  if (account.kind === 'service') {
    throw new ForbiddenError(SERVICE_ACCOUNT_SIGN_IN_MESSAGE);
  }
  if (account.isDisabled) {
    throw new ForbiddenError(DISABLED_ACCOUNT_SIGN_IN_MESSAGE);
  }

  const org = await findActiveOrgById(user.orgId);
  if (!org) {
    throw new NotFoundError('Organization not found');
  }
  const accountType = org?.accountType;
  // Prefer DB role; default member so post-migration login still issues a valid claim.
  const role = normalizeUserRole(user.role) ?? 'member';

  return authJwtGenerator(
    jwtSecret,
    user.email,
    user._id,
    user.orgId,
    user.fullName,
    accountType,
    role,
  );
}

export async function generateFetchConfigAuthToken(
  user: Record<string, any>,
  scopedJwtSecret: string,
) {
  return fetchConfigJwtGenerator(user._id, user.orgId, scopedJwtSecret);
}
