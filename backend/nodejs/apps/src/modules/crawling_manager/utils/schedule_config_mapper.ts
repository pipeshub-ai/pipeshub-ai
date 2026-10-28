import { isValidId } from '../../../libs/repositories/ids';
import { toDriverId } from '../../../libs/repositories/mongo';
import { CrawlingScheduleType } from '../schema/enums';
import { ICrawlingSchedule } from '../schema/interface';

export const SCHEDULED_STRATEGY = 'SCHEDULED';

export interface ConnectorScheduledConfigBlock {
  intervalMinutes?: number;
  timezone?: string;
}

export interface ConnectorSyncBlock {
  selectedStrategy?: string;
  customValues?: Record<string, unknown>;
  scheduledConfig?: ConnectorScheduledConfigBlock;
  [key: string]: unknown;
}

export const isScheduledSyncStrategy = (
  sync: ConnectorSyncBlock | null | undefined,
): boolean =>
  !!sync &&
  typeof sync.selectedStrategy === 'string' &&
  sync.selectedStrategy.toUpperCase() === SCHEDULED_STRATEGY;

// Stable sentinel used when a caller passes a non-ObjectId identifier such as
// 'system'. Using a fixed zero-value keeps createdBy/lastUpdatedBy consistent
// across calls and prevents noise in any future diff or audit log.
const SYSTEM_OBJECT_ID = toDriverId('000000000000000000000000');

const toObjectId = (userId: string): ReturnType<typeof toDriverId> => {
  if (isValidId(userId)) {
    return toDriverId(userId);
  }
  return SYSTEM_OBJECT_ID;
};

/**
 * Convert a connector's saved `sync` block to an ICrawlingSchedule.
 * Returns `null` when the connector isn't configured for SCHEDULED sync,
 * or when the schedule values are missing/invalid.
 */
export const buildCrawlingScheduleFromSync = (
  sync: ConnectorSyncBlock | null | undefined,
  ownerUserId: string,
): ICrawlingSchedule | null => {
  if (!isScheduledSyncStrategy(sync)) return null;

  const scheduled = sync!.scheduledConfig;
  const intervalMinutes = Number(scheduled?.intervalMinutes);
  if (!Number.isFinite(intervalMinutes) || intervalMinutes < 1) return null;

  const timezone =
    typeof scheduled?.timezone === 'string' && scheduled.timezone.trim().length
      ? scheduled.timezone
      : 'UTC';

  const ownerObjectId = toObjectId(ownerUserId);

  return {
    scheduleType: CrawlingScheduleType.INTERVAL,
    isEnabled: true,
    createdBy: ownerObjectId,
    lastUpdatedBy: ownerObjectId,
    scheduleConfig: {
      intervalMinutes: Math.floor(intervalMinutes),
      timezone,
    },
  };
};
