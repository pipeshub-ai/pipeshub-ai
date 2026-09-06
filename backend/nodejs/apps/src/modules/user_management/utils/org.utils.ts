import mongoose from 'mongoose';
import { Org, type IOrg } from '../schema/org.schema';

export async function findActiveOrgById(orgId: unknown): Promise<IOrg | null> {
  if (typeof orgId !== 'string' || !mongoose.isValidObjectId(orgId)) {
    return null;
  }
  return Org.findOne({
    _id: orgId,
    isDeleted: false,
  });
}
