import { RESOLVED_CONFIG_FEATURE } from '../../domain/worker-compat';
import { harnessLogMeta, HarnessLogger } from '../harness-log';
import {
  servedOrUnknown,
  type WorkerFeatureAvailability,
} from '../worker-feature-availability';

/** Whether a live worker advertises a feature, and where a withheld requirement is counted. */
export interface PinWorkerFeatures {
  live: WorkerFeatureAvailability['live'];
  metrics: Pick<WorkerFeatureAvailability['metrics'], 'withheld'>;
  /**
   * The policy store is the record (`CONTROL_PLANE_POLICY_STORE=on`): the KV
   * documents a run without its pin reads are no longer written, so the pin
   * is required whatever workers advertise and the run waits for one that reads it.
   */
  pinOnly?: boolean;
}

/**
 * Whether a pinned run requires `resolved_config` and carries its pin in its
 * spec (the F2 rule, for a root and for a child alike): unless a live worker
 * shows it lacks the feature (ADR-CAP-01 D2), so a deploy that upgrades Node first runs
 * the run on the spec's legacy fields instead of queueing it for a worker
 * that does not exist yet. No availability wired, or `pinOnly`: required.
 */
export async function pinReadByWorker(
  workers: PinWorkerFeatures | undefined,
  logger: HarnessLogger,
  run: { orgId: string; sessionId?: string | undefined },
): Promise<boolean> {
  if (workers === undefined || workers.pinOnly === true) return true;
  if (await servedOrUnknown(workers, RESOLVED_CONFIG_FEATURE)) return true;
  workers.metrics.withheld(RESOLVED_CONFIG_FEATURE);
  logger.info(
    'run.resolved_config_withheld',
    harnessLogMeta('run.resolved_config_withheld', {
      orgId: run.orgId,
      sessionId: run.sessionId,
      reason: 'no_live_worker',
    }),
  );
  return false;
}
