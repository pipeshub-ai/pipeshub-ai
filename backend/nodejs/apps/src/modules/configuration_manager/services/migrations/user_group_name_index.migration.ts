import { Types } from 'mongoose';
import { Logger } from '../../../../libs/services/logger.service';
import {
  ACTIVE_GROUP_NAME_INDEX,
  UserGroups,
} from '../../../user_management/schema/userGroup.schema';

const MONGO_DUPLICATE_KEY = 11000;
const MONGO_NAMESPACE_NOT_FOUND = 26;

export type UserGroupNameIndexStatus =
  | 'already_present'
  | 'created'
  | 'skipped_duplicates'
  | 'failed';

export interface UserGroupNameIndexResult {
  status: UserGroupNameIndexStatus;
  duplicateSets: number;
  errored: number;
}

interface DuplicateNameSet {
  _id: { orgId: Types.ObjectId; name: string };
  groupIds: Types.ObjectId[];
}

export const DUPLICATE_GROUP_NAMES_MESSAGE =
  'Skipped adding the unique user group name index: some active user groups in the same organization share a name. ' +
  'Rename the user groups listed in "duplicates" so that no two active groups in one organization share a name, then restart the service. ' +
  'Nothing was renamed or deleted. Until then, two requests arriving at the same moment can still create user groups with the same name.';

/**
 * Enforces "one active user group name per organization" in the database, so two
 * simultaneous create or rename requests cannot both pass the app-level check.
 * Runs every boot: cheap once the index exists, and keeps reporting existing
 * duplicates until an admin renames them.
 */
export class UserGroupNameIndexMigration {
  constructor(private readonly logger: Logger) {}

  async run(): Promise<UserGroupNameIndexResult> {
    try {
      if (await this.indexExists()) {
        return { status: 'already_present', duplicateSets: 0, errored: 0 };
      }

      const duplicates = await this.findDuplicateNames();
      if (duplicates.length > 0) {
        return this.skipForDuplicates(duplicates);
      }

      try {
        await UserGroups.collection.createIndex(
          ACTIVE_GROUP_NAME_INDEX.keys,
          ACTIVE_GROUP_NAME_INDEX.options,
        );
      } catch (error) {
        if (
          (error as { code?: unknown } | null)?.code !== MONGO_DUPLICATE_KEY
        ) {
          throw error;
        }
        // A duplicate was written between the check and the index build.
        return this.skipForDuplicates(await this.findDuplicateNames());
      }
      this.logger.info('Created unique index on active user group names', {
        index: ACTIVE_GROUP_NAME_INDEX.options.name,
      });
      return { status: 'created', duplicateSets: 0, errored: 0 };
    } catch (error) {
      this.logger.error('Unique user group name index migration failed', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      return { status: 'failed', duplicateSets: 0, errored: 1 };
    }
  }

  private async indexExists(): Promise<boolean> {
    try {
      const indexes = await UserGroups.collection.listIndexes().toArray();
      return indexes.some(
        (index: { name?: string }) =>
          index.name === ACTIVE_GROUP_NAME_INDEX.options.name,
      );
    } catch (error) {
      // The collection does not exist yet on a fresh install; createIndex makes it.
      if (
        (error as { code?: unknown } | null)?.code === MONGO_NAMESPACE_NOT_FOUND
      ) {
        return false;
      }
      throw error;
    }
  }

  private async findDuplicateNames(): Promise<DuplicateNameSet[]> {
    return UserGroups.aggregate<DuplicateNameSet>([
      { $match: ACTIVE_GROUP_NAME_INDEX.options.partialFilterExpression },
      {
        $group: {
          _id: { orgId: '$orgId', name: '$name' },
          groupIds: { $push: '$_id' },
          count: { $sum: 1 },
        },
      },
      { $match: { count: { $gt: 1 } } },
      { $project: { groupIds: 1 } },
    ]);
  }

  private skipForDuplicates(
    duplicates: DuplicateNameSet[],
  ): UserGroupNameIndexResult {
    this.logger.error(DUPLICATE_GROUP_NAMES_MESSAGE, {
      duplicates: duplicates.map((set) => ({
        orgId: String(set._id.orgId),
        name: set._id.name,
        groupIds: set.groupIds.map((id) => String(id)),
      })),
    });
    return {
      status: 'skipped_duplicates',
      duplicateSets: duplicates.length,
      errored: 0,
    };
  }
}
