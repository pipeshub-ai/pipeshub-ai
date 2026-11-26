import { WorkerFeature } from '../domain/worker-compat';

/** When a worker last advertised a feature, on the store's clock, read against its `now`. */
export interface FeatureSighting {
  seenAt: number | null;
  now: number;
}

/**
 * Where live workers' advertisements are kept between claims (DUR2). There is no
 * worker registry: a worker shows what it runs on every claim poll (idle) and,
 * through its attempt's features, on every lease renewal (busy). This keeps the
 * latest sighting per feature so every Node replica reads the same fleet.
 */
export interface WorkerFeatureSightings {
  /** Live workers advertised `features` (a claim or a renewal); keeps the latest time. */
  saw(features: readonly WorkerFeature[]): Promise<void>;
  sighting(feature: WorkerFeature): Promise<FeatureSighting>;
}

export type StrandedOutcome = 'relaxed' | 'failed';

/** What an operator watches (DUR2): the gap between Node and the workers. Must not throw. */
export interface WorkerFeatureMetrics {
  live(feature: WorkerFeature, live: boolean): void;
  /** A run that would have required `feature` was admitted without it: no live worker advertises it. */
  withheld(feature: WorkerFeature): void;
  stranded(feature: WorkerFeature, outcome: StrandedOutcome): void;
}
