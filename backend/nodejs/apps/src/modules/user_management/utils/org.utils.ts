import mongoose from 'mongoose';
import { Org, type IOrg } from '../schema/org.schema';

export async function findActiveOrgById(orgId: unknown): Promise<IOrg | null> {
  // A freshly provisioned user is a Mongoose document, so its orgId is an
  // ObjectId rather than a string. Normalise before the guard: otherwise the
  // first SSO login of every JIT-provisioned user fails with "Organization
  // not found" and only the retry succeeds.
  const id =
    orgId !== null && typeof orgId === 'object' && typeof (orgId as { toString?: unknown }).toString === 'function'
      ? String(orgId)
      : orgId;
  if (typeof id !== 'string' || !mongoose.isValidObjectId(id)) {
    return null;
  }
  return Org.findOne({
    _id: id,
    isDeleted: false,
  });
}
