import { getOrgRepository } from '../../../libs/repositories/org';
import { isNativeId, isValidId } from '../../../libs/repositories/ids';
import type { OrgDto } from '../../../libs/repositories/org';

export async function findActiveOrgById(
  orgId: unknown,
): Promise<OrgDto | null> {
  // A freshly provisioned user is a Mongoose document, so its orgId is an
  // ObjectId rather than a string. Normalise before the guard: otherwise the
  // first SSO login of every JIT-provisioned user fails with "Organization
  // not found" and only the retry succeeds. Only a real ObjectId is
  // normalised, so any other object still falls through to the guard and
  // returns null without touching the database.
  const id = isNativeId(orgId) ? String(orgId) : orgId;
  if (typeof id !== 'string' || !isValidId(id)) {
    return null;
  }
  return getOrgRepository().getLive(id);
}
