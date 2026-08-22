import { NotFoundError } from '../../../libs/errors/http.errors';
import {
  authJwtGenerator,
  fetchConfigJwtGenerator,
} from '../../../libs/utils/createJwt';
import { Org } from '../../user_management/schema/org.schema';
import { normalizeUserRole } from '../../user_management/services/user-admin.service';

export async function generateAuthToken(
  user: Record<string, any>,
  jwtSecret: string,
) {
  // look up for
  const org = await Org.findOne({ orgId: user.orgId, isDeleted: false });
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
