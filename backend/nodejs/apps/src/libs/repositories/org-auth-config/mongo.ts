import { OrgAuthConfig } from '../../../modules/auth/schema/orgAuthConfiguration.schema';
import { sessionOf } from '../mongo';
import { toPlain } from '../plain';
import type { Tx } from '../uow';
import type {
  AuthStepDto,
  OrgAuthConfigDto,
  OrgAuthConfigRepository,
} from './types';

const dto = (row: unknown): OrgAuthConfigDto => toPlain<OrgAuthConfigDto>(row);

export class MongoOrgAuthConfigRepository implements OrgAuthConfigRepository {
  async get(orgId: string): Promise<OrgAuthConfigDto | null> {
    const row = await OrgAuthConfig.findOne({ orgId });
    return row ? dto(row) : null;
  }

  async getLive(orgId: string): Promise<OrgAuthConfigDto | null> {
    const row = await OrgAuthConfig.findOne({ orgId, isDeleted: false });
    return row ? dto(row) : null;
  }

  async getFirstLive(): Promise<OrgAuthConfigDto | null> {
    const row = await OrgAuthConfig.findOne({ isDeleted: false }).lean().exec();
    return row ? dto(row) : null;
  }

  async anyLive(): Promise<boolean> {
    return (await OrgAuthConfig.countDocuments({ isDeleted: false })) > 0;
  }

  async removedSignInMethods(orgId: string): Promise<string[]> {
    const row = await OrgAuthConfig.findOne({ orgId, isDeleted: false })
      .select('removedSignInMethods')
      .lean<{ removedSignInMethods?: string[] }>();
    return row?.removedSignInMethods ?? [];
  }

  async allowsPassword(orgId: string): Promise<boolean> {
    return !!(await OrgAuthConfig.exists({
      orgId,
      authSteps: {
        $elemMatch: {
          allowedMethods: { $elemMatch: { type: 'password' } },
        },
      },
    }));
  }

  async createDefault(
    orgId: string,
    domain: string | undefined,
    tx?: Tx,
  ): Promise<void> {
    const orgAuth = new OrgAuthConfig({
      orgId,
      domain,
      authSteps: [
        {
          order: 1,
          allowedMethods: [{ type: 'password' }],
        },
      ],
      isDeleted: false,
    });
    const session = sessionOf(tx);
    if (session) await orgAuth.save({ session });
    else await orgAuth.save();
  }

  async replaceSteps(
    orgId: string,
    steps: AuthStepDto[],
  ): Promise<OrgAuthConfigDto | null> {
    const config = await OrgAuthConfig.findOne({ orgId });
    if (!config) return null;
    config.authSteps = steps as typeof config.authSteps;
    await config.save();
    return dto(config);
  }
}
