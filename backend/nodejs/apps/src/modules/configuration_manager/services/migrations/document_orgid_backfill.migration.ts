import mongoose from 'mongoose';
import { Logger } from '../../../../libs/services/logger.service';
import { KeyValueStoreService } from '../../../../libs/services/keyValueStore.service';
import { Org } from '../../../user_management/schema/org.schema';
import { DocumentModel } from '../../../storage/schema/document.schema';
import { configPaths } from '../../paths/paths';

const MIGRATION_FLAG_DONE = 'true';

/**
 * One-time backfill that stamps every document in the `documents` collection
 * that is missing an `orgId` with the oldest organisation's `_id`.
 *
 * Background: before the `orgId` field was formally declared in
 * `document.schema.ts`, Mongoose's strict mode silently stripped it on every
 * `create()` call, so older documents were saved without the field.
 *
 * Strategy:
 *  1. Skip immediately if the KV-store completion flag is set.
 *  2. Skip if no documents are missing `orgId` (already clean).
 *  3. Find the first (oldest) org by `createdAt` ascending — this is the
 *     most conservative choice for single-org installations and avoids
 *     arbitrary assignment in multi-org setups.
 *  4. Run a single `updateMany` to stamp all affected documents.
 *  5. Set the completion flag so this never runs again.
 *
 * The migration is idempotent: re-running after a partial failure is safe
 * because `updateMany` only touches documents still missing `orgId`.
 */
export class DocumentOrgIdBackfillMigration {
  constructor(
    private readonly logger: Logger,
    private readonly kvStore: KeyValueStoreService,
  ) {}

  async run(): Promise<{ updated: number; skipped: boolean }> {
    // Guard: skip if a previous successful run already set the flag.
    try {
      const flag = await this.kvStore.get<string>(
        configPaths.documentOrgIdBackfillMigration,
      );
      if (flag === MIGRATION_FLAG_DONE) {
        this.logger.info(
          'Document orgId backfill migration already completed; skipping',
        );
        return { updated: 0, skipped: true };
      }
    } catch (error) {
      this.logger.warn(
        'Failed to read document orgId backfill migration flag; proceeding with idempotent run',
        { error: error instanceof Error ? error.message : 'Unknown error' },
      );
    }

    this.logger.info('Starting document orgId backfill migration');

    // Check how many documents are actually missing orgId before touching anything.
    const missingCount = await DocumentModel.countDocuments({
      $or: [{ orgId: { $exists: false } }, { orgId: null }],
    });

    if (missingCount === 0) {
      this.logger.info(
        'All documents already have orgId — document orgId backfill migration not needed',
      );
      await this.markDone();
      return { updated: 0, skipped: true };
    }

    this.logger.info(
      `Found ${missingCount} document(s) missing orgId; proceeding with backfill`,
    );

    // Find the oldest org — project only _id so we get a clean ObjectId without
    // loading the full document. .lean() returns a plain JS object where _id is
    // correctly typed as Types.ObjectId by Mongoose.
    const firstOrg = await Org.findOne({ isDeleted: false }, { _id: 1 })
      .sort({ createdAt: 1 })
      .lean<{ _id: mongoose.Types.ObjectId }>();

    if (!firstOrg) {
      this.logger.warn(
        'No organisation found in the database — document orgId backfill skipped. ' +
          'Migration will retry on next boot.',
      );
      return { updated: 0, skipped: true };
    }

    const orgId = firstOrg._id;

    this.logger.info('Using org for backfill', {
      orgId: orgId.toString(),
    });

    // Single atomic updateMany — only touches documents still missing orgId.
    const result = await DocumentModel.updateMany(
      { $or: [{ orgId: { $exists: false } }, { orgId: null }] },
      { $set: { orgId } },
    );

    const updated = result.modifiedCount;
    this.logger.info(`Document orgId backfill complete — ${updated} document(s) updated`, {
      orgId: firstOrg._id.toString(),
      modifiedCount: updated,
      matchedCount: result.matchedCount,
    });

    await this.markDone();
    return { updated, skipped: false };
  }

  private async markDone(): Promise<void> {
    try {
      await this.kvStore.set(
        configPaths.documentOrgIdBackfillMigration,
        MIGRATION_FLAG_DONE,
      );
    } catch (error) {
      this.logger.error(
        'Failed to persist document orgId backfill completion flag — migration will re-run on next boot',
        { error: error instanceof Error ? error.message : 'Unknown error' },
      );
    }
  }
}
