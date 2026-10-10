import { Logger } from '../../../../libs/services/logger.service';
import { KeyValueStoreService } from '../../../../libs/services/keyValueStore.service';
import { configPaths } from '../../paths/paths';
import { ChatSession } from '../../../enterprise_search/schema/chat.session.schema';
import { Project } from '../../../projects/schema/project.schema';

export interface AclVersionMigrationResult {
  chatSessions: number;
  projects: number;
  errored: number;
}

/**
 * Stamps `aclVersion: 0` on chat sessions and projects that predate the field
 * (`/migrations/acl_version_v1`). `updateMany` filtered on `$exists:false` is
 * naturally idempotent and never overwrites a bumped version. Loaders still
 * treat a missing value as 0 because `.lean()` skips schema defaults.
 */
export class AclVersionMigration {
  constructor(
    private readonly logger: Logger,
    private readonly kvStore: KeyValueStoreService,
  ) {}

  async run(): Promise<AclVersionMigrationResult> {
    const result: AclVersionMigrationResult = {
      chatSessions: 0,
      projects: 0,
      errored: 0,
    };

    try {
      const done = await this.kvStore.get<string>(
        configPaths.aclVersionMigration,
      );
      if (done !== null && done !== '') {
        this.logger.info('aclVersion migration already completed; skipping');
        return result;
      }
    } catch (error) {
      this.logger.warn(
        'Failed to read aclVersion migration flag; running anyway (idempotent)',
        {
          error: error instanceof Error ? error.message : String(error),
        },
      );
    }

    const targets = [
      { key: 'chatSessions', model: ChatSession },
      { key: 'projects', model: Project },
    ] as const;
    for (const { key, model } of targets) {
      try {
        const res = await model.collection.updateMany(
          { aclVersion: { $exists: false } },
          { $set: { aclVersion: 0 } },
        );
        result[key] = res.modifiedCount;
      } catch (error) {
        result.errored += 1;
        this.logger.error(
          `aclVersion migration failed for ${key}; retrying next boot`,
          {
            error: error instanceof Error ? error.message : String(error),
          },
        );
      }
    }

    this.logger.info('aclVersion migration pass finished', { ...result });
    if (result.errored === 0) {
      try {
        await this.kvStore.set(
          configPaths.aclVersionMigration,
          JSON.stringify(result),
        );
      } catch (error) {
        this.logger.warn(
          'aclVersion migration succeeded but failed to persist the flag; will retry on next boot',
          {
            error: error instanceof Error ? error.message : String(error),
          },
        );
      }
    }
    return result;
  }
}
