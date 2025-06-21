import { fetchConfigJwtGenerator } from '../../../libs/utils/createJwt';

export async function generateFetchConfigToken(
  user: Record<string, any>,
  scopedJwtSecret: string,
) {
  return fetchConfigJwtGenerator(user.userId, user.orgId, scopedJwtSecret);
}
