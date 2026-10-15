import { Logger } from '../../../libs/services/logger.service';
import { CrawlingSchedulerService } from './crawling_service';
import { CrawlingScheduleType } from '../schema/enums';
import {
  ConnectorSyncBlock,
  buildCrawlingScheduleFromSync,
  isScheduledSyncStrategy,
} from '../utils/schedule_config_mapper';

export type ScheduleReconcileOutcome =
  | 'scheduled'
  | 'removed'
  | 'noop'
  | 'skipped';

export interface ScheduleReconcileInput {
  connector: string; // connector type, e.g. 'Confluence'
  connectorId: string;
  orgId: string;
  userId: string;
  isActive: boolean;
  sync: ConnectorSyncBlock | null | undefined;
}

/**
 * Reconcile the BullMQ crawling job for a connector against its saved
 * sync config. Idempotent — `scheduleJob` removes any existing job before
 * creating a new one, and we explicitly remove on disable / non-SCHEDULED.
 */
export const reconcileConnectorSchedule = async (
  scheduler: CrawlingSchedulerService,
  logger: Logger,
  input: ScheduleReconcileInput,
): Promise<ScheduleReconcileOutcome> => {
  const { connector, connectorId, orgId, userId, isActive, sync } = input;
  const ctx = { connector, connectorId, orgId };

  if (!connector || !connectorId || !orgId || !userId) {
    logger.warn('reconcileConnectorSchedule called with missing identifiers', ctx);
    return 'skipped';
  }

  const selectedStrategy = sync?.selectedStrategy ?? '(none)';
  const wantsSchedule = isActive && isScheduledSyncStrategy(sync);

  if (!wantsSchedule) {
    const skipReason = !isActive
      ? 'connector was deactivated'
      : `sync strategy is "${selectedStrategy}" (not SCHEDULED)`;

    logger.info(`Schedule reconcile: ${skipReason} — checking for existing job to remove`, {
      ...ctx,
      isActive,
      selectedStrategy,
    });

    try {
      const existing = await scheduler.getJobStatus(connector, connectorId, orgId);
      if (!existing) {
        logger.info(`Schedule reconcile: no existing job found — nothing to remove (${skipReason})`, ctx);
        return 'noop';
      }
      await scheduler.removeJob(connector, connectorId, orgId);
      logger.info(`Crawling job removed: ${skipReason}`, ctx);
      return 'removed';
    } catch (error) {
      logger.error('Failed to remove crawling job during reconcile', {
        ...ctx,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  const schedule = buildCrawlingScheduleFromSync(sync, userId);
  if (!schedule) {
    logger.warn(
      'Connector has SCHEDULED strategy but scheduledConfig is invalid or missing — skipping job creation',
      {
        ...ctx,
        scheduledConfig: sync?.scheduledConfig ?? null,
      },
    );
    return 'skipped';
  }

  const isInterval = schedule.scheduleType === CrawlingScheduleType.INTERVAL;
  const intervalCfg = isInterval ? schedule.scheduleConfig : undefined;
  const intervalMinutes = intervalCfg?.intervalMinutes;
  const timezone = intervalCfg?.timezone ?? 'UTC';

  try {
    await scheduler.scheduleJob(
      connector,
      connectorId,
      schedule,
      orgId,
      userId,
    );
    logger.info(
      `Crawling job scheduled: connector activated with ${intervalMinutes ?? '?'}min interval`,
      {
        ...ctx,
        scheduleType: schedule.scheduleType,
        intervalMinutes,
        timezone,
      },
    );
    return 'scheduled';
  } catch (error) {
    logger.error('Failed to schedule crawling job during reconcile', {
      ...ctx,
      error: error instanceof Error ? error.message : 'Unknown error',
    });
    throw error;
  }
};

