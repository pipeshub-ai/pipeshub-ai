import { Users } from '../../../modules/user_management/schema/users.schema';
import { Org } from '../../../modules/user_management/schema/org.schema';
import { OrgAuthConfig } from '../../../modules/auth/schema/orgAuthConfiguration.schema';
import { toPlain } from '../plain';
import type {
  AuthConfigMetricRow,
  OrgMetricRow,
  OrgMetricsJobRepository,
} from './types';

export class MongoOrgMetricsJobRepository implements OrgMetricsJobRepository {
  async countUsersByOrg(): Promise<
    Array<{ _id: string | null; count: number }>
  > {
    const rows = await Users.aggregate<{ _id: string | null; count: number }>([
      { $match: { isDeleted: { $ne: true } } },
      { $group: { _id: '$orgId', count: { $sum: 1 } } },
    ]);
    return toPlain<Array<{ _id: string | null; count: number }>>(rows);
  }

  async listLiveOrgs(): Promise<OrgMetricRow[]> {
    const rows = await Org.find(
      { isDeleted: { $ne: true } },
      { accountType: 1, domain: 1 },
    ).lean();
    return toPlain<OrgMetricRow[]>(rows);
  }

  async listLiveAuthConfigs(): Promise<AuthConfigMetricRow[]> {
    const rows = await OrgAuthConfig.find(
      { isDeleted: { $ne: true } },
      { orgId: 1, authSteps: 1 },
    ).lean();
    return toPlain<AuthConfigMetricRow[]>(rows);
  }
}
