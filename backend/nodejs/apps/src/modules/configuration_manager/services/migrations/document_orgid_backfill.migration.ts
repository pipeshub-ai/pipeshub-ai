import { Logger } from '../../../../libs/services/logger.service';
import { KeyValueStoreService } from '../../../../libs/services/keyValueStore.service';
import { configPaths } from '../../paths/paths';
import { DocumentModel } from '../../../storage/schema/document.schema';
import { Org } from '../../../user_management/schema/org.schema';

const MIGRATION_FLAG_DONE = 'true';

/**
 * One-time backfill: stamp orgId on storage documents created before the
 * field was added to the schema. OSS is single-org so the one non-deleted
 * org's _id is used for all documents missing the field.
 *
 * Idempotent via KV flag + query filter (only touches documents where
 * orgId does not exist).
 */
export class DocumentOrgIdBackfillMigration {
  constructor(
    private readonly logger: Logger,
    private readonly kvStore: KeyValueStoreService,
  ) {}

  async run(): Promise<{ updated: number; errored: number }> {
    try {
      const flag = await this.kvStore.get<string>(
        configPaths.documentOrgIdMigration,
      );
      if (flag === MIGRATION_FLAG_DONE) {
        this.logger.info(
          'Document orgId backfill migration already completed; skipping',
        );
        return { updated: 0, errored: 0 };
      }
    } catch (error) {
      this.logger.warn(
        'Failed to read document orgId migration flag; proceeding with idempotent run',
        { error: error instanceof Error ? error.message : 'Unknown error' },
      );
    }

    this.logger.info('Starting document orgId backfill migration');

    let updated = 0;
    let errored = 0;

    try {
      const missingCount = await DocumentModel.countDocuments({
        $or: [{ orgId: { $exists: false } }, { orgId: null }],
      });

      if (missingCount === 0) {
        this.logger.info(
          'No storage documents missing orgId; marking migration done',
        );
        await this.kvStore.set(
          configPaths.documentOrgIdMigration,
          MIGRATION_FLAG_DONE,
        );
        return { updated: 0, errored: 0 };
      }

      this.logger.info(
        `Found ${missingCount} storage documents missing orgId`,
      );

      const org = await Org.findOne({ isDeleted: { $ne: true } })
        .select('_id')
        .lean();

      if (!org) {
        errored++;
        this.logger.error(
          'No org found for document orgId backfill — cannot determine orgId',
        );
        return { updated: 0, errored };
      }

      const result = await DocumentModel.updateMany(
        { $or: [{ orgId: { $exists: false } }, { orgId: null }] },
        { $set: { orgId: org._id } },
      );
      updated = result.modifiedCount ?? 0;

      this.logger.info(
        `Backfilled ${updated} storage documents with orgId ${String(org._id)}`,
      );
    } catch (error) {
      errored++;
      this.logger.error('Document orgId backfill migration failed', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }

    if (errored > 0) {
      this.logger.warn(
        'Document orgId backfill finished with errors — flag NOT written, will retry on next boot',
        { updated, errored },
      );
      return { updated, errored };
    }

    try {
      await this.kvStore.set(
        configPaths.documentOrgIdMigration,
        MIGRATION_FLAG_DONE,
      );
    } catch (error) {
      this.logger.warn(
        'Document orgId backfill succeeded but failed to write flag; will retry on next boot',
        { error: error instanceof Error ? error.message : 'Unknown error' },
      );
    }

    this.logger.info('Document orgId backfill migration completed', {
      updated,
    });
    return { updated, errored };
  }
}
