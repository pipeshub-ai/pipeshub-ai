import mongoose from 'mongoose';
import { Org, type IOrg } from '../schema/org.schema';

export async function findActiveOrgById(orgId: unknown): Promise<IOrg | null> {
  // A freshly provisioned user is a Mongoose document, so its orgId is an
  // ObjectId rather than a string. Normalise before the guard: otherwise the
  // first SSO login of every JIT-provisioned user fails with "Organization
  // not found" and only the retry succeeds. Only a real ObjectId is
  // normalised, so any other object still falls through to the guard and
  // returns null without touching the database.
  const id =
    orgId instanceof mongoose.Types.ObjectId ? orgId.toHexString() : orgId;
  if (typeof id !== 'string' || !mongoose.isValidObjectId(id)) {
    return null;
  }
  return Org.findOne({
    _id: id,
    isDeleted: false,
  });
}
