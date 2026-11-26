import { StoreClock, systemStoreClock } from '../domain/execution';
import {
  ENGINE_FEATURE,
  LIVENESS_FEATURE,
  WorkerFeature,
} from '../domain/worker-compat';
import {
  WorkerFeatureMetrics,
  WorkerFeatureSightings,
} from '../ports/worker-features';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';

/**
 * How long a feature stays live after a worker last showed it. An idle worker
 * polls for claims at least every 5 s; a busy one renews each lease every 20 s
 * (lease 60 s). A live worker of either kind shows up well inside a minute.
 */
export const WORKER_FEATURE_WINDOW_MS = 60_000;
export const noopWorkerFeatureMetrics: WorkerFeatureMetrics = {
  live: () => undefined,
  withheld: () => undefined,
  stranded: () => undefined,
};

/** One store write per feature per Node process in this period, however many polls arrive. */
export const SIGHTING_WRITE_INTERVAL_MS = 10_000;
/** Admission reads the store at most this often per feature. */
export const AVAILABILITY_CACHE_MS = 5_000;

export interface WorkerFeatureAvailabilityOptions {
  sightings: WorkerFeatureSightings;
  logger?: HarnessLogger;
  metrics?: WorkerFeatureMetrics;
  clock?: StoreClock;
  windowMs?: number;
}

/**
 * Whether a live worker advertises a feature (DUR2, the deploy-order guard for
 * `agent_code`). Fed by claims and renewals, which every worker already makes;
 * read by admission (require a feature only a live worker can serve) and the
 * sweeper (release runs stranded on a feature nobody serves any more).
 */
export class WorkerFeatureAvailability {
  private readonly written = new Map<WorkerFeature, number>();
  private readonly cached = new Map<
    WorkerFeature,
    { at: number; live: boolean }
  >();
  private readonly known = new Map<WorkerFeature, boolean>();
  private writeFailed = false;

  private readonly sightings: WorkerFeatureSightings;
  private readonly logger: HarnessLogger;
  readonly metrics: WorkerFeatureMetrics;
  private readonly clock: StoreClock;
  private readonly windowMs: number;

  constructor(options: WorkerFeatureAvailabilityOptions) {
    this.sightings = options.sightings;
    this.logger = options.logger ?? silentHarnessLogger;
    this.metrics = options.metrics ?? noopWorkerFeatureMetrics;
    this.clock = options.clock ?? systemStoreClock;
    this.windowMs = options.windowMs ?? WORKER_FEATURE_WINDOW_MS;
  }

  /** A worker showed `features` (a claim poll or a lease renewal). Never throws. */
  async observe(features: readonly WorkerFeature[]): Promise<void> {
    const now = this.clock.now();
    const due = features.filter(
      (feature) =>
        now - (this.written.get(feature) ?? -Infinity) >=
        SIGHTING_WRITE_INTERVAL_MS,
    );
    if (due.length === 0) return;
    for (const feature of due) this.written.set(feature, now);
    try {
      await this.sightings.saw(due);
      this.writeFailed = false;
    } catch (error) {
      for (const feature of due) this.written.delete(feature);
      if (!this.writeFailed) {
        this.writeFailed = true;
        this.logger.warn(
          'worker.sighting_failed',
          harnessLogMeta('worker.sighting_failed', {
            reason: 'store_unavailable',
            errorType: error instanceof Error ? error.name : typeof error,
          }),
        );
      }
    }
  }

  /**
   * A worker advertised `feature` within the window. A store that cannot be read
   * answers `false`: requiring a feature nobody is known to serve strands runs.
   */
  async live(feature: WorkerFeature): Promise<boolean> {
    const now = this.clock.now();
    const cached = this.cached.get(feature);
    if (cached !== undefined && now - cached.at < AVAILABILITY_CACHE_MS) {
      return cached.live;
    }
    let live = false;
    let lastSeenAt: number | null = null;
    try {
      const sighting = await this.sightings.sighting(feature);
      lastSeenAt = sighting.seenAt;
      live =
        sighting.seenAt !== null &&
        sighting.now - sighting.seenAt <= this.windowMs;
    } catch (error) {
      this.logger.warn(
        'worker.sighting_unreadable',
        harnessLogMeta('worker.sighting_unreadable', {
          feature,
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
    }
    this.cached.set(feature, { at: now, live });
    this.transition(feature, live, lastSeenAt);
    return live;
  }

  private transition(
    feature: WorkerFeature,
    live: boolean,
    lastSeenAt: number | null,
  ): void {
    this.metrics.live(feature, live);
    const before = this.known.get(feature);
    this.known.set(feature, live);
    if (before === live) return;
    if (live) {
      if (before === false) {
        this.logger.info(
          'worker.feature_live',
          harnessLogMeta('worker.feature_live', { feature }),
        );
      }
      return;
    }
    this.logger.warn(
      'worker.feature_gap',
      harnessLogMeta('worker.feature_gap', {
        feature,
        reason: 'no_live_worker',
        lastSeenAt:
          lastSeenAt === null ? null : new Date(lastSeenAt).toISOString(),
        windowMs: this.windowMs,
      }),
    );
  }
}

/** What admission knows of a guarded feature: a live worker serves it, a live worker lacks it, or no worker told. */
export type GuardedFeatureState = 'live' | 'absent' | 'unknown';

/**
 * Whether a live worker serves a guarded feature (ADR-CAP-01 D2). `absent` only when a live worker shows it lacks
 * it (every loop worker advertises LIVENESS_FEATURE with its others); before any worker is seen, a stack just
 * started, or while sightings cannot be read, it is `unknown`, and admission requires it as if served: a guarded
 * requirement no worker serves is relaxed by the sweeper after the window (`unstrand`), so unknown costs a wait at
 * most, where withholding it would cost the run the feature for good.
 */
export async function guardedFeatureState(
  workers: Pick<WorkerFeatureAvailability, 'live'>,
  feature: WorkerFeature,
): Promise<GuardedFeatureState> {
  if (await workers.live(feature)) return 'live';
  return (await workers.live(LIVENESS_FEATURE)) ? 'absent' : 'unknown';
}

/** Whether admission requires a guarded feature: unless a live worker proves it lacks it (ADR-CAP-01 D2). */
export const servedOrUnknown = async (
  workers: Pick<WorkerFeatureAvailability, 'live'>,
  feature: WorkerFeature,
): Promise<boolean> =>
  (await guardedFeatureState(workers, feature)) !== 'absent';

/**
 * Whether live workers prove that none runs engines: a loop worker is live and none advertises `executor_engine`.
 * Before any worker is seen, or while the sightings cannot be read, it is unknown, and unknown is not absent
 * (ADR-CAP-01 D2).
 */
export async function engineExecutorAbsent(
  workers: Pick<WorkerFeatureAvailability, 'live'> | undefined,
): Promise<boolean> {
  if (workers === undefined) return false;
  try {
    return (await guardedFeatureState(workers, ENGINE_FEATURE)) === 'absent';
  } catch {
    return false;
  }
}
