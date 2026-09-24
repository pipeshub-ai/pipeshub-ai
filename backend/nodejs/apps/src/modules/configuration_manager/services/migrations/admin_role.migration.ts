import { Logger } from '../../../../libs/services/logger.service';
import { KeyValueStoreService } from '../../../../libs/services/keyValueStore.service';
import { configPaths } from '../../paths/paths';
import { UserGroups } from '../../../user_management/schema/userGroup.schema';
import { Users } from '../../../user_management/schema/users.schema';

const MIGRATION_FLAG_DONE = 'true';

/**
 * One-time backfill: copy admin UserGroup membership onto User.role, then
 * soft-delete type=admin groups. Idempotent via KV flag + safe re-runs
 * (already-admin users / already-deleted groups are no-ops).
 */
export class AdminRoleMigration {
  constructor(
    private readonly logger: Logger,
    private readonly kvStore: KeyValueStoreService,
  ) {}

  async run(): Promise<{
    adminGroupsProcessed: number;
    usersPromoted: number;
    usersDefaultedToMember: number;
    adminGroupsSoftDeleted: number;
    errored: number;
  }> {
    try {
      const flag = await this.kvStore.get<string>(configPaths.adminRoleMigration);
      if (flag === MIGRATION_FLAG_DONE) {
        this.logger.info('Admin-role migration already completed; skipping');
        return {
          adminGroupsProcessed: 0,
          usersPromoted: 0,
          usersDefaultedToMember: 0,
          adminGroupsSoftDeleted: 0,
          errored: 0,
        };
      }
    } catch (error) {
      this.logger.warn(
        'Failed to read admin-role migration flag; proceeding with idempotent run',
        { error: error instanceof Error ? error.message : 'Unknown error' },
      );
    }

    this.logger.info('Starting admin-role migration');

    let adminGroupsProcessed = 0;
    let usersPromoted = 0;
    let usersDefaultedToMember = 0;
    let adminGroupsSoftDeleted = 0;
    let errored = 0;

    try {
      const adminGroups = await UserGroups.find({
        type: 'admin',
        isDeleted: { $ne: true },
      })
        .select('_id orgId users')
        .lean();

      for (const group of adminGroups) {
        try {
          adminGroupsProcessed += 1;
          const orgId = group.orgId;
          const adminUserIds = (group.users ?? []).filter(Boolean);

          if (adminUserIds.length > 0) {
            const promoteResult = await Users.updateMany(
              { _id: { $in: adminUserIds }, orgId, isDeleted: { $ne: true } },
              { $set: { role: 'admin' } },
            );
            usersPromoted += promoteResult.modifiedCount ?? 0;

            // Fail closed: never soft-delete the legacy admin group if the org
            // would be left with zero role=admin users after promote.
            const adminCount = await Users.countDocuments({
              orgId,
              role: 'admin',
              isDeleted: { $ne: true },
            });
            if (adminCount < 1) {
              errored += 1;
              this.logger.error(
                'Admin-role migration left org with no admins; skipping soft-delete',
                {
                  groupId: String(group._id),
                  orgId: String(orgId),
                  adminUserIds: adminUserIds.map(String),
                  matchedCount: promoteResult.matchedCount ?? 0,
                  modifiedCount: promoteResult.modifiedCount ?? 0,
                },
              );
              continue;
            }
          }

          const memberResult = await Users.updateMany(
            {
              orgId,
              isDeleted: { $ne: true },
              $or: [{ role: { $exists: false } }, { role: null }],
            },
            { $set: { role: 'member' } },
          );
          usersDefaultedToMember += memberResult.modifiedCount ?? 0;

          const deleteResult = await UserGroups.updateOne(
            { _id: group._id },
            { $set: { isDeleted: true } },
          );
          if (deleteResult.modifiedCount) {
            adminGroupsSoftDeleted += 1;
          }
        } catch (error) {
          errored += 1;
          this.logger.error('Admin-role migration failed for admin group', {
            groupId: String(group._id),
            orgId: String(group.orgId),
            error: error instanceof Error ? error.message : 'Unknown error',
          });
        }
      }

      // Orgs that never had an admin group (or already soft-deleted): still
      // default any users missing/null role to member (same unset filter as per-org pass).
      const defaultAllResult = await Users.updateMany(
        {
          isDeleted: { $ne: true },
          $or: [{ role: { $exists: false } }, { role: null }],
        },
        { $set: { role: 'member' } },
      );
      usersDefaultedToMember += defaultAllResult.modifiedCount ?? 0;
    } catch (error) {
      errored += 1;
      this.logger.error('Admin-role migration failed', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }

    if (errored > 0) {
      this.logger.warn(
        'Admin-role migration finished with errors — completion flag NOT written, will retry on next boot',
        {
          adminGroupsProcessed,
          usersPromoted,
          usersDefaultedToMember,
          adminGroupsSoftDeleted,
          errored,
        },
      );
      return {
        adminGroupsProcessed,
        usersPromoted,
        usersDefaultedToMember,
        adminGroupsSoftDeleted,
        errored,
      };
    }

    try {
      await this.kvStore.set(configPaths.adminRoleMigration, MIGRATION_FLAG_DONE);
    } catch (error) {
      this.logger.warn(
        'Admin-role migration succeeded but failed to write completion flag; will retry on next boot',
        { error: error instanceof Error ? error.message : 'Unknown error' },
      );
    }

    this.logger.info('Admin-role migration completed', {
      adminGroupsProcessed,
      usersPromoted,
      usersDefaultedToMember,
      adminGroupsSoftDeleted,
    });

    return {
      adminGroupsProcessed,
      usersPromoted,
      usersDefaultedToMember,
      adminGroupsSoftDeleted,
      errored,
    };
  }
}
