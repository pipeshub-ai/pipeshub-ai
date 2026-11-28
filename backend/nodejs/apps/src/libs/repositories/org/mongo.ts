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
import { Logger } from '../../services/logger.service';
import { sessionOf } from '../mongo';
import { isDuplicateKeyError } from '../../utils/mongo.utils';
import { callerSlug, slugRefused } from './slug';
import { UniqueConstraintError } from '../errors';
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

// The admin-role lease is a lock, not organisation data.
const dto = (row: unknown): OrgDto => {
  const org = toPlain<OrgDto & { adminRoleLease?: unknown }>(row);
  delete org.adminRoleLease;
  return org;
};
/** The unique slug index, raised by the driver or already mapped (`mapMongoError`). */
const isSlugTaken = (error: unknown): boolean =>
  (isDuplicateKeyError(error) &&
    (error as { keyPattern?: Record<string, unknown> }).keyPattern?.slug !==
      undefined) ||
  (error instanceof UniqueConstraintError && error.metadata?.key === 'slug');
const logoDto = (row: unknown): OrgLogoDto => toPlain<OrgLogoDto>(row);

export class MongoOrgRepository implements OrgRepository {
  /** Every organisation the install has had, removed ones too; never one whose create failed. */
  async count(): Promise<number> {
    return Org.countDocuments({ pendingCreate: { $ne: true } });
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
      .select('-_id accountType')
      .lean()
      .exec();
    return row ? toPlain<Pick<OrgDto, 'accountType'>>(row) : null;
  }

  async getNames(
    orgId: string,
  ): Promise<Pick<OrgDto, 'registeredName' | 'shortName'> | null> {
    const row = await Org.findOne({ _id: orgId, isDeleted: false })
      .select('-_id registeredName shortName')
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
      .select('-_id onBoardingStatus')
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
    const slug = callerSlug(input.slug);
    // `input.slug` is the only way in: a slug among the org fields is dropped, as PostgreSQL never reads it
    const { slug: _ignored, ...fields } = input.org;
    // Written hidden and made live by the last write (below), so no write of a failed create can leave a live
    // organisation, even one the server applies after the cleanup ran.
    const org = new Org({
      ...fields,
      ...(slug === undefined ? {} : { slug }),
      _id: input.ids.orgId,
      isDeleted: true,
      pendingCreate: true,
    });
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
    const docs = [
      org,
      authConfig,
      allUsersGroup,
      standardUsersGroup,
      adminUser,
      credentials,
    ];
    let started = 0;
    try {
      for (const doc of docs) {
        // Counted before the save: an insert the server applied but whose reply was lost must be removed too.
        started += 1;
        await doc.save(opts);
      }
      // Matches only the hidden row as it was written: applied after the cleanup removed it or hid it again (which moves
      // updatedAt on), it changes nothing.
      await Org.updateOne(
        {
          _id: org._id,
          pendingCreate: true,
          updatedAt: org.get('updatedAt') as Date | undefined,
        },
        { $set: { isDeleted: false }, $unset: { pendingCreate: 1 } },
        opts,
      );
      org.isDeleted = false;
      org.pendingCreate = undefined;
    } catch (error) {
      // Without a transaction nothing undoes the rows already written, so remove them: the organisation is created whole or not at all.
      if (!session) {
        // Hide the organisation again before removing anything: the activation may have applied with its reply lost, and
        // removing the administrator of a live organisation is worse than leaving the create whole.
        try {
          await Org.updateOne(
            { _id: org._id },
            {
              $set: {
                isDeleted: true,
                pendingCreate: true,
                updatedAt: new Date(
                  Math.max(
                    Date.now(),
                    ((org.get('updatedAt') as Date | undefined)?.getTime() ??
                      0) + 1,
                  ),
                ),
              },
            },
            { timestamps: false },
          );
        } catch (hideError) {
          Logger.getInstance().warn(
            'Organisation create failed and the organisation could not be hidden again; its rows are left as they are',
            {
              id: String(org._id),
              error:
                hideError instanceof Error
                  ? hideError.message
                  : String(hideError),
            },
          );
          throw error;
        }
        const tried = docs.slice(0, started);
        const undone = await Promise.allSettled(
          tried.map((doc) => doc.deleteOne()),
        );
        tried.forEach((doc, i) => {
          const r = undone[i];
          if (r?.status === 'rejected')
            Logger.getInstance().warn(
              'Organisation create failed and a row it wrote could not be removed',
              {
                collection: doc.collection.collectionName,
                id: String(doc._id),
                error:
                  r.reason instanceof Error
                    ? r.reason.message
                    : String(r.reason),
              },
            );
        });
      }
      throw isSlugTaken(error) ? slugRefused() : error;
    }
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
