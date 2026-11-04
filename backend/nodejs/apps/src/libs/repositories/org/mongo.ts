import mongoose from 'mongoose';
import { Org } from '../../../modules/user_management/schema/org.schema';
import { Users } from '../../../modules/user_management/schema/users.schema';
import { UserGroups } from '../../../modules/user_management/schema/userGroup.schema';
import { OrgLogos } from '../../../modules/user_management/schema/orgLogo.schema';
import {
  AuthMethodType,
  OrgAuthConfig,
} from '../../../modules/auth/schema/orgAuthConfiguration.schema';
import { UserCredentials } from '../../../modules/auth/schema/userCredentials.schema';
import { sessionOf } from '../mongo';
import { toPlain } from '../plain';
import type { Tx } from '../uow';
import type {
  NewOrgIds,
  NewOrgWithAdmin,
  OrgDto,
  OrgLogoDto,
  OrgPatch,
  OrgRepository,
} from './types';

const dto = (row: unknown): OrgDto => toPlain<OrgDto>(row);
const logoDto = (row: unknown): OrgLogoDto => toPlain<OrgLogoDto>(row);

export class MongoOrgRepository implements OrgRepository {
  async count(): Promise<number> {
    return Org.countDocuments();
  }

  async getLive(orgId: string): Promise<OrgDto | null> {
    const row = await Org.findOne({ _id: orgId, isDeleted: false });
    return row ? dto(row) : null;
  }

  async isActive(orgId: string): Promise<boolean> {
    const row = await Org.findOne({ _id: orgId, isDeleted: { $ne: true } })
      .select('_id')
      .lean()
      .exec();
    return row !== null;
  }

  async getFirstLive(opts: { hydrate?: boolean } = {}): Promise<OrgDto | null> {
    const row = opts.hydrate
      ? await Org.findOne({ isDeleted: false })
      : await Org.findOne({ isDeleted: false }).lean().exec();
    return row ? dto(row) : null;
  }

  /** `count` stops at 2 (enough to tell one organisation from several); `oldestId` is the oldest when there is exactly one. */
  async liveSummary(): Promise<{ count: number; oldestId: string | null }> {
    const rows = await Org.find({ isDeleted: false }, { _id: 1 })
      .limit(2)
      .lean()
      .exec();
    return {
      count: rows.length,
      oldestId: rows[0] ? String(rows[0]._id) : null,
    };
  }

  allocateIds(): NewOrgIds {
    return {
      orgId: new mongoose.Types.ObjectId().toString(),
      adminUserId: new mongoose.Types.ObjectId().toString(),
    };
  }

  async getAccountType(
    orgId: string,
  ): Promise<Pick<OrgDto, 'accountType'> | null> {
    const row = await Org.findOne({ _id: orgId, isDeleted: false })
      .select('accountType')
      .lean()
      .exec();
    return row ? toPlain<Pick<OrgDto, 'accountType'>>(row) : null;
  }

  async getNames(
    orgId: string,
  ): Promise<Pick<OrgDto, 'registeredName' | 'shortName'> | null> {
    const row = await Org.findOne({ _id: orgId, isDeleted: false })
      .select('registeredName shortName')
      .lean()
      .exec();
    return row
      ? toPlain<Pick<OrgDto, 'registeredName' | 'shortName'>>(row)
      : null;
  }

  async getOnboardingStatus(
    orgId: string,
  ): Promise<Pick<OrgDto, 'onBoardingStatus'> | null> {
    const row = await Org.findOne({ _id: orgId, isDeleted: false })
      .select('onBoardingStatus')
      .lean();
    return row ? toPlain<Pick<OrgDto, 'onBoardingStatus'>>(row) : null;
  }

  async update(orgId: string, patch: OrgPatch): Promise<OrgDto | null> {
    const row = await Org.findByIdAndUpdate(orgId, patch, {
      returnDocument: 'after',
    });
    return row ? dto(row) : null;
  }

  async setOnboardingStatus(
    orgId: string,
    status: string,
  ): Promise<OrgDto | null> {
    const org = await Org.findOne({ _id: orgId, isDeleted: false });
    if (!org) return null;
    org.onBoardingStatus = status;
    await org.save();
    return dto(org);
  }

  async softDelete(orgId: string): Promise<OrgDto | null> {
    const org = await Org.findOne({ _id: orgId, isDeleted: false });
    if (!org) return null;
    org.isDeleted = true;
    await org.save();
    return dto(org);
  }

  async createWithAdmin(
    input: NewOrgWithAdmin,
    tx?: Tx,
  ): Promise<{
    org: OrgDto;
    adminUserId: string;
    adminEmail: string;
  }> {
    const org = new Org({ ...input.org, _id: input.ids.orgId });
    const adminUser = new Users({
      _id: input.ids.adminUserId,
      fullName: input.admin.fullName,
      email: input.admin.email,
      orgId: org._id,
      role: 'admin',
    });
    const credentials = new UserCredentials({
      userId: adminUser._id,
      orgId: org._id,
      isDeleted: false,
      hashedPassword: input.passwordHash,
      ipAddress: input.ipAddress,
    });
    const allUsersGroup = new UserGroups({
      type: 'everyone',
      name: 'everyone',
      orgId: org._id,
      users: [adminUser._id],
    });
    const standardUsersGroup = new UserGroups({
      type: 'standard',
      name: 'standard',
      orgId: org._id,
      users: [],
    });
    const authConfig = new OrgAuthConfig({
      orgId: org._id,
      authSteps: [
        {
          order: 1,
          allowedMethods: [{ type: AuthMethodType.PASSWORD }],
        },
      ],
    });

    const session = sessionOf(tx);
    const opts = session ? { session } : undefined;
    await authConfig.save(opts);
    await allUsersGroup.save(opts);
    await standardUsersGroup.save(opts);
    await adminUser.save(opts);
    await credentials.save(opts);
    await org.save(opts);
    return {
      org: dto(org),
      adminUserId: String(adminUser._id),
      adminEmail: adminUser.email,
    };
  }

  async getLogo(orgId: string): Promise<OrgLogoDto | null> {
    const row = await OrgLogos.findOne({ orgId }).lean().exec();
    return row ? logoDto(row) : null;
  }

  async setLogo(orgId: string, logo: string, mimeType: string): Promise<void> {
    await OrgLogos.findOneAndUpdate(
      { orgId },
      { orgId, logo, mimeType },
      { returnDocument: 'after', upsert: true },
    );
  }

  async removeLogo(orgId: string): Promise<OrgLogoDto | null> {
    const row = await OrgLogos.findOne({ orgId }).exec();
    if (!row) return null;
    row.logo = null;
    row.mimeType = null;
    await row.save();
    return logoDto(row);
  }
}
