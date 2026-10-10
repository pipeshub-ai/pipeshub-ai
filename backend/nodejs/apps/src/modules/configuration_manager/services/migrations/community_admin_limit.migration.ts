import { Logger } from '../../../../libs/services/logger.service';
import { KeyValueStoreService } from '../../../../libs/services/keyValueStore.service';
import { userActivitiesType } from '../../../../libs/utils/userActivities.utils';
import { configPaths } from '../../paths/paths';
import { Users } from '../../../user_management/schema/users.schema';
import { UserActivities } from '../../../auth/schema/userActivities.schema';
import { UserAdminRepository } from '../../../user_management/repositories/user-admin.repository';
import { NotificationContainer } from '../../../notification/container/notification.container';
import {
  ADMIN_LIMIT_ENFORCEMENT_DATE,
  MAX_ORG_ADMINS,
  selectAdminToRetain,
} from '../../../user_management/services/user-admin.service';

const MIGRATION_FLAG_DONE = 'true';

export interface CommunityAdminLimitResult {
  skipped: boolean;
  orgsProcessed: number;
  adminsDemoted: number;
  errored: number;
}

/**
 * Community Edition only. From ADMIN_LIMIT_ENFORCEMENT_DATE, brings every org
 * down to MAX_ORG_ADMINS by making the other admins members, keeping the one
 * selectAdminToRetain picks. Before that date it does nothing and leaves the
 * flag unset, so the first boot on or after the date runs it.
 */
export class CommunityAdminLimitMigration {
  constructor(
    private readonly logger: Logger,
    private readonly kvStore: KeyValueStoreService,
    private readonly now: () => Date = () => new Date(),
  ) {}

  async run(): Promise<CommunityAdminLimitResult> {
    const result: CommunityAdminLimitResult = {
      skipped: true,
      orgsProcessed: 0,
      adminsDemoted: 0,
      errored: 0,
    };

    if (this.now() < ADMIN_LIMIT_ENFORCEMENT_DATE) {
      this.logger.info('Community admin-limit migration not due yet; skipping', {
        enforcementDate: ADMIN_LIMIT_ENFORCEMENT_DATE.toISOString(),
      });
      return result;
    }

    try {
      const flag = await this.kvStore.get<string>(configPaths.communityAdminLimitMigration);
      if (flag === MIGRATION_FLAG_DONE) {
        this.logger.info('Community admin-limit migration already completed; skipping');
        return result;
      }
    } catch (error) {
      this.logger.warn(
        'Failed to read community admin-limit migration flag; proceeding with idempotent run',
        { error: error instanceof Error ? error.message : 'Unknown error' },
      );
    }

    result.skipped = false;
    this.logger.info('Starting community admin-limit migration');

    let orgIds: unknown[] = [];
    try {
      const overLimit = await Users.aggregate<{ _id: unknown }>([
        {
          $match: {
            role: 'admin',
            isDeleted: { $ne: true },
            kind: { $ne: 'service' },
          },
        },
        { $group: { _id: '$orgId', count: { $sum: 1 } } },
        { $match: { count: { $gt: MAX_ORG_ADMINS } } },
      ]);
      orgIds = overLimit.map((row) => row._id).filter(Boolean);
    } catch (error) {
      result.errored += 1;
      this.logger.error('Community admin-limit migration could not list orgs', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }

    for (const rawOrgId of orgIds) {
      const orgId = String(rawOrgId);
      try {
        result.orgsProcessed += 1;
        result.adminsDemoted += await this.limitOrg(orgId);
      } catch (error) {
        result.errored += 1;
        this.logger.error('Community admin-limit migration failed for org', {
          orgId,
          error: error instanceof Error ? error.message : 'Unknown error',
        });
      }
    }

    if (result.errored > 0) {
      this.logger.warn(
        'Community admin-limit migration finished with errors — completion flag NOT written, will retry on next boot',
        result,
      );
      return result;
    }

    try {
      await this.kvStore.set(configPaths.communityAdminLimitMigration, MIGRATION_FLAG_DONE);
    } catch (error) {
      this.logger.warn(
        'Community admin-limit migration succeeded but failed to write completion flag; will retry on next boot',
        { error: error instanceof Error ? error.message : 'Unknown error' },
      );
    }

    this.logger.info('Community admin-limit migration completed', result);
    return result;
  }

  private async limitOrg(orgId: string): Promise<number> {
    const admins = await UserAdminRepository.findActiveAdmins(orgId);
    if (admins.length <= MAX_ORG_ADMINS) return 0;

    const contactEmail = await UserAdminRepository.findOrgContactEmail(orgId);
    const retained = selectAdminToRetain(admins, contactEmail);
    const demoted = admins.filter((admin) => admin !== retained);

    const recordRoleChange = () =>
      UserActivities.insertMany(
        demoted.map((admin) => ({
          orgId,
          userId: admin._id,
          email: admin.email,
          activityType: userActivitiesType.ROLE_CHANGED,
          ipAddress: 'system',
        })),
      );

    // ROLE_CHANGED is what ends a session whose token still says admin.
    // Recorded before the demotion: if that write fails, the next boot finds
    // these admins again and records it again; recorded after the demotion
    // instead, a failure in between would leave their admin tokens working,
    // since the retry no longer sees them as admins.
    await recordRoleChange();
    await Users.updateMany(
      { _id: { $in: demoted.map((admin) => admin._id) }, orgId, role: 'admin' },
      { $set: { role: 'member' } },
    );
    // Again after, for a token issued between the two writes, while the
    // admin role was still stored. The demotion has happened by now, so a
    // failure here is logged rather than failing the org.
    try {
      await recordRoleChange();
    } catch (error) {
      this.logger.warn('Community admin-limit migration could not re-record the role change', {
        orgId,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }

    const notificationService = NotificationContainer.getNotificationService();
    for (const admin of demoted) {
      notificationService?.emitForceLogout(String(admin._id), 'role_changed');
    }

    this.logger.info('Community admin-limit migration demoted extra admins', {
      orgId,
      retainedAdminId: String(retained?._id),
      demotedAdminIds: demoted.map((admin) => String(admin._id)),
    });
    return demoted.length;
  }
}
