import { Logger } from '../../../libs/services/logger.service';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { classifyError, levelFor } from '../../../libs/utils/error-class';
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
      // The error object: the logger prints its stack only at ERROR.
      logger.warn(`${RECONCILE_LOG_MESSAGE}: retrying after error`, {
        ...ctx,
        attempt: attempt + 1,
        error,
      });
      await new Promise((resolve) => setTimeout(resolve, retryDelaysMs[attempt]));
    }
  }
};

/**
 * Make the BullMQ crawling schedule for a connector match its saved sync
 * config. Every call ends in exactly one `RECONCILE_LOG_MESSAGE` line:
 * `outcome` success or failure, the case in `reason` (docs/logging.md "Crawling
 * jobs (Node)"). Queue errors are retried, then rethrown.
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
  // Passed explicitly: a reconcile also runs outside any request (the sweep, a connector event).
  const ctx = {
    connector_type: connector,
    connector_id: connectorId,
    org_id: orgId,
  };

  const report = (
    result: ScheduleReconcileOutcome | 'queue_failed',
    extra: Record<string, unknown> = {},
  ): void => {
    const failed =
      result === 'queue_failed' ||
      result === 'invalid_input' ||
      result === 'invalid_config';
    const errorClass =
      result === 'queue_failed' ? classifyError(extra.error) : undefined;
    const level = errorClass ? levelFor(errorClass) : failed ? 'warn' : 'info';
    logger[level](RECONCILE_LOG_MESSAGE, {
      ...ctx,
      // `outcome` takes the logger's values only; the case is the typed `reason`.
      outcome: failed ? 'failure' : 'success',
      reason: result,
      ...(errorClass && { error_class: errorClass }),
      is_active: isActive,
      selected_strategy: sync?.selectedStrategy ?? null,
      interval_minutes: sync?.scheduledConfig?.intervalMinutes ?? null,
      duration_ms: Date.now() - started,
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
      report('invalid_config');
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
    // The scheduler refusing the schedule (assertSchedulable) is the config's fault, not the queue's.
    if (error instanceof BadRequestError) report('invalid_config');
    else report('queue_failed', { error });
    throw error;
  }
};
