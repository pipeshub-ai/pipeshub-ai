import { Logger } from '../../../libs/services/logger.service';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { CrawlingSchedulerService } from './crawling_service';
import {
  ConnectorSyncBlock,
  buildCrawlingScheduleFromSync,
  isScheduledSyncStrategy,
} from '../utils/schedule_config_mapper';

export type ScheduleReconcileOutcome =
  | 'scheduled'
  | 'unchanged'
  | 'removed'
  | 'noop'
  | 'invalid_input'
  | 'invalid_config';

export interface ScheduleReconcileInput {
  connector: string; // connector type, e.g. 'Confluence'
  connectorId: string;
  orgId: string;
  userId: string;
  isActive: boolean;
  sync: ConnectorSyncBlock | null | undefined;
}

/** Fixed message for every reconcile result, so one query finds them all. */
export const RECONCILE_LOG_MESSAGE = 'connector schedule reconcile';

const RETRY_DELAYS_MS = [200, 1_000];

const errorFields = (error: unknown): { error: string; stack?: string } => ({
  error: error instanceof Error ? error.message : String(error),
  stack: error instanceof Error ? error.stack : undefined,
});

// Validation errors are the input's fault; retrying them only delays the report.
const withRetry = async <T>(
  operation: () => Promise<T>,
  retryDelaysMs: number[],
  logger: Logger,
  ctx: Record<string, unknown>,
): Promise<T> => {
  for (let attempt = 0; ; attempt++) {
    try {
      return await operation();
    } catch (error) {
      if (error instanceof BadRequestError || attempt >= retryDelaysMs.length) {
        throw error;
      }
      logger.warn(`${RECONCILE_LOG_MESSAGE}: retrying after error`, {
        ...ctx,
        attempt: attempt + 1,
        ...errorFields(error),
      });
      await new Promise((resolve) => setTimeout(resolve, retryDelaysMs[attempt]));
    }
  }
};

/**
 * Make the BullMQ crawling schedule for a connector match its saved sync
 * config. Every call ends in exactly one `RECONCILE_LOG_MESSAGE` log line
 * carrying the outcome; failures and invalid input or config are logged at
 * error. Queue errors are retried, then rethrown.
 */
export const reconcileConnectorSchedule = async (
  scheduler: CrawlingSchedulerService,
  logger: Logger,
  input: ScheduleReconcileInput,
  { retryDelaysMs = RETRY_DELAYS_MS }: { retryDelaysMs?: number[] } = {},
): Promise<ScheduleReconcileOutcome> => {
  const started = Date.now();
  const { connector, connectorId, orgId, isActive, sync } = input;
  // Only stamped on the job as createdBy; a missing owner must not cost the schedule.
  const userId = input.userId || 'system';
  const ctx = { connector, connectorId, orgId };

  const report = (
    outcome: ScheduleReconcileOutcome | 'failed',
    extra: Record<string, unknown> = {},
  ): void => {
    const isProblem =
      outcome === 'failed' || outcome === 'invalid_input' || outcome === 'invalid_config';
    const log = isProblem ? logger.error.bind(logger) : logger.info.bind(logger);
    log(RECONCILE_LOG_MESSAGE, {
      ...ctx,
      outcome,
      isActive,
      selectedStrategy: sync?.selectedStrategy ?? null,
      intervalMinutes: sync?.scheduledConfig?.intervalMinutes ?? null,
      durationMs: Date.now() - started,
      ...extra,
    });
  };

  if (!connector || !connectorId || !orgId) {
    report('invalid_input');
    return 'invalid_input';
  }

  try {
    if (!isActive || !isScheduledSyncStrategy(sync)) {
      const removed = await withRetry(
        () => scheduler.removeConnectorSchedules(connector, connectorId, orgId),
        retryDelaysMs,
        logger,
        ctx,
      );
      const outcome = removed ? 'removed' : 'noop';
      report(outcome);
      return outcome;
    }

    const schedule = buildCrawlingScheduleFromSync(sync, userId);
    if (!schedule) {
      report('invalid_config', { scheduledConfig: sync?.scheduledConfig ?? null });
      return 'invalid_config';
    }

    const outcome = await withRetry(
      () =>
        scheduler.upsertRepeatingSchedule(connector, connectorId, schedule, orgId, userId),
      retryDelaysMs,
      logger,
      ctx,
    );
    report(outcome);
    return outcome;
  } catch (error) {
    report('failed', errorFields(error));
    throw error;
  }
};
