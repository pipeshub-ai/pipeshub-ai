import mongoose, { PipelineStage, Types } from 'mongoose';
import type { Logger } from '../../../libs/services/logger.service';
import { OAuthApp, OAuthAppStatus } from '../schema/oauth.app.schema';

const LOG_PREFIX = 'OAuth orphan creators migration';

export interface SoftDeleteOAuthOrphanCreatorsResult {
  skipped: boolean;
  skipReason?: 'env' | 'not_connected';
  foundCount: number;
  matchedCount: number;
  modifiedCount: number;
  dryRun: boolean;
}

type MigrationLogger = Pick<Logger, 'info' | 'warn' | 'error'>;

function migrationLog(
  logger: MigrationLogger | undefined,
  level: 'info' | 'warn' | 'error',
  message: string,
  meta?: Record<string, unknown>,
): void {
  const msg = `${LOG_PREFIX}: ${message}`;
  if (logger) {
    logger[level](msg, meta);
    return;
  }
  if (level === 'error') {
    console.error(msg, meta);
  } else if (level === 'warn') {
    console.warn(msg, meta);
  } else {
    console.log(msg, meta);
  }
}

/**
 * Soft-delete OAuth apps whose creator user document is missing or soft-deleted.
 * Uses the active mongoose connection (does not connect/disconnect).
 *
 * Set `SKIP_OAUTH_ORPHAN_CREATORS_MIGRATION=1` to skip at startup.
 */
export async function runSoftDeleteOAuthAppsOrphanCreators(params?: {
  dryRun?: boolean;
  logDetailIds?: boolean;
  logger?: MigrationLogger;
}): Promise<SoftDeleteOAuthOrphanCreatorsResult> {
  const dryRun = params?.dryRun ?? false;
  const logger = params?.logger;

  const noop = (
    skipped: boolean,
    skipReason?: 'env' | 'not_connected',
  ): SoftDeleteOAuthOrphanCreatorsResult => ({
    skipped,
    skipReason,
    foundCount: 0,
    matchedCount: 0,
    modifiedCount: 0,
    dryRun,
  });

  if (
    process.env.SKIP_OAUTH_ORPHAN_CREATORS_MIGRATION === '1' ||
    process.env.SKIP_OAUTH_ORPHAN_CREATORS_MIGRATION === 'true'
  ) {
    migrationLog(
      logger,
      'info',
      'skipped (SKIP_OAUTH_ORPHAN_CREATORS_MIGRATION)',
    );
    return noop(true, 'env');
  }

  if (mongoose.connection.readyState !== 1) {
    migrationLog(logger, 'warn', 'skipped (mongoose not connected)', {
      readyState: mongoose.connection.readyState,
    });
    return noop(true, 'not_connected');
  }

  const pipeline: PipelineStage[] = [
    { $match: { isDeleted: false } },
    {
      $lookup: {
        from: 'users',
        localField: 'createdBy',
        foreignField: '_id',
        as: 'creator',
      },
    },
    {
      $match: {
        $or: [{ creator: { $size: 0 } }, { 'creator.0.isDeleted': true }],
      },
    },
    { $project: { _id: 1, clientId: 1, name: 1 } },
  ];

  const toFix = await OAuthApp.aggregate<{
    _id: Types.ObjectId;
    clientId: string;
    name: string;
  }>(pipeline);

  migrationLog(
    logger,
    'info',
    `found ${toFix.length} app(s) with missing or deleted creator`,
  );

  if (params?.logDetailIds && toFix.length > 0) {
    migrationLog(logger, 'info', 'matched apps', {
      apps: toFix.map((d) => ({
        _id: d._id.toString(),
        clientId: d.clientId,
        name: d.name,
      })),
    });
  }

  if (toFix.length === 0) {
    return {
      skipped: false,
      foundCount: 0,
      matchedCount: 0,
      modifiedCount: 0,
      dryRun,
    };
  }

  const ids = toFix.map((d) => d._id);

  if (dryRun) {
    migrationLog(
      logger,
      'info',
      `[dry-run] would update ${ids.length} document(s)`,
    );
    return {
      skipped: false,
      foundCount: toFix.length,
      matchedCount: 0,
      modifiedCount: 0,
      dryRun: true,
    };
  }

  const result = await OAuthApp.updateMany(
    { _id: { $in: ids }, isDeleted: false },
    {
      $set: {
        isDeleted: true,
        status: OAuthAppStatus.REVOKED,
      },
    },
  );

  migrationLog(
    logger,
    'info',
    `updated matched=${result.matchedCount} modified=${result.modifiedCount}`,
  );

  return {
    skipped: false,
    foundCount: toFix.length,
    matchedCount: result.matchedCount,
    modifiedCount: result.modifiedCount,
    dryRun: false,
  };
}
