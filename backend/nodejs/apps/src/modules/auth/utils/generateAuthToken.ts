import { NotFoundError } from '../../../libs/errors/http.errors';
import {
  authJwtGenerator,
  fetchConfigJwtGenerator,
} from '../../../libs/utils/createJwt';
import { Org } from '../../user_management/schema/org.schema';

export async function generateAuthToken(
  user: Record<string, any>,
  jwtKey: string,
) {
  // look up for
  const org = await Org.findOne({ orgId: user.orgId, isDeleted: false });
  if (!org) {
    throw new NotFoundError('Organization not found');
  }
  const accountType = org?.accountType;

  return authJwtGenerator(
    jwtKey,
    user.email,
    user._id,
    user.orgId,
    user.fullName,
    accountType,
  );
}

export async function generateFetchConfigAuthToken(
  user: Record<string, any>,
  scopedJwtKey: string,
) {
  return fetchConfigJwtGenerator(user._id, user.orgId, scopedJwtKey);
}
