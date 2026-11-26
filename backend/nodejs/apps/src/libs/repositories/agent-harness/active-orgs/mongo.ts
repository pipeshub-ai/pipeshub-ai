import { Org } from '../../../../modules/user_management/schema/org.schema';

/** Every org not deleted: the orgs a whole control-plane data migration covers. */
export async function activeOrgIds(): Promise<string[]> {
  const orgs = await Org.find({ isDeleted: { $ne: true } }, { _id: 1 })
    .lean()
    .exec();
  return orgs.map((org) => String(org._id));
}
