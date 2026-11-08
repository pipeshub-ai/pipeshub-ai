import {
  UserActivities,
  IUserActivity,
} from '../../../modules/auth/schema/userActivities.schema';
import {
  SESSION_INVALIDATING_ACTIVITIES,
  userActivitiesType,
} from '../../utils/userActivities.utils';
import { toPlain } from '../plain';
import type { ActivityDto, ActivityRepository, NewActivity } from './types';

export class MongoActivityRepository implements ActivityRepository {
  async record(entry: NewActivity): Promise<void> {
    await UserActivities.create(entry as unknown as IUserActivity);
  }

  async recordMany(entries: NewActivity[]): Promise<void> {
    await UserActivities.insertMany(entries);
  }

  async latestSessionEnding(
    userId: string,
    orgId: string,
  ): Promise<ActivityDto | null> {
    const row = await UserActivities.findOne({
      userId,
      orgId,
      isDeleted: false,
      activityType: { $in: [...SESSION_INVALIDATING_ACTIVITIES] },
    })
      .sort({ createdAt: -1, _id: -1 }) // sort by most recent first
      .lean()
      .exec();
    return row ? toPlain<ActivityDto>(row) : null;
  }

  async listSessionEndingSince(
    userIds: string[],
    since: Date,
  ): Promise<ActivityDto[]> {
    const rows = await UserActivities.find({
      userId: { $in: userIds },
      isDeleted: false,
      activityType: { $in: [...SESSION_INVALIDATING_ACTIVITIES] },
      createdAt: { $gte: since },
    })
      .select('userId orgId activityType createdAt')
      .sort({ createdAt: -1, _id: -1 })
      .lean()
      .exec();
    return rows.map((r) => toPlain<ActivityDto>(r));
  }

  async latestPasswordChange(
    userId: string,
    orgId: string,
  ): Promise<ActivityDto | null> {
    const row = await UserActivities.findOne({
      userId,
      orgId,
      isDeleted: false,
      activityType: userActivitiesType.PASSWORD_CHANGED,
    })
      .sort({ createdAt: -1, _id: -1 }) // sort by most recent first
      .lean()
      .exec();
    return row ? toPlain<ActivityDto>(row) : null;
  }
}
