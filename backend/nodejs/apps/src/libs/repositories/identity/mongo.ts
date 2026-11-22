import { Org } from '../../../modules/user_management/schema/org.schema';
import {
  UserActivities,
  IUserActivity,
} from '../../../modules/auth/schema/userActivities.schema';
import { MongoCredentialRepository } from '../credential/mongo';
import type { IdentityPort, PreOrgActivity } from './types';

export class MongoIdentityPort
  extends MongoCredentialRepository
  implements IdentityPort
{
  async oldestOrgId(): Promise<string | null> {
    const org = await Org.findOne({ isDeleted: { $ne: true } })
      .sort({ createdAt: 1, _id: 1 })
      .select('_id')
      .lean();
    return org ? String(org._id) : null;
  }

  async orgCount(liveOnly: boolean): Promise<number> {
    return Org.countDocuments(liveOnly ? { isDeleted: { $ne: true } } : {});
  }

  async recordPreOrgActivity(entry: PreOrgActivity): Promise<void> {
    await UserActivities.create(entry as unknown as IUserActivity);
  }
}
