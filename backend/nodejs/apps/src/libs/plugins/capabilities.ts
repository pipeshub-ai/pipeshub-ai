import { host } from './registry';

/**
 * Questions application code may ask of a plug-in without importing it.
 *
 * `getCapability(name)` returns `undefined` when no loaded plug-in answers, and
 * the caller must then behave as a build that does not have the feature.
 * Keep each capability narrow and named for the product feature, not for the
 * plug-in that happens to provide it.
 */
export interface PlatformCapabilities {
  'data-collection': DataCollectionCapability;
  metrics: MetricsCapability;
}

/** Whether this build can send usage data anywhere at all. */
export interface DataCollectionCapability {
  /** False when the deployment has switched collection off for the whole process. */
  isActive(): boolean;
}

export type MetricLabels = Record<string, string | number>;

export interface MetricDefinition {
  name: string;
  help: string;
  labelNames: string[];
  /** Counter series that exist at 0 whenever the registry is read. */
  zeroSeries?: MetricLabels[];
}

export interface HistogramDefinition extends MetricDefinition {
  buckets: number[];
}

/**
 * A process registry of operational metrics (the agent harness's, the control plane's), scraped from `/metrics`.
 * Application code reaches it through `libs/metrics` (`opsMetrics`), which answers no-ops without it.
 */
export interface MetricsCapability {
  createCounter(def: MetricDefinition): {
    inc(labels?: MetricLabels, value?: number): void;
  };
  createGauge(def: MetricDefinition): {
    set(labels: MetricLabels, value: number): void;
    reset(): void;
  };
  createHistogram(def: HistogramDefinition): {
    observe(labels: MetricLabels, value: number): void;
  };
  serialize(): Promise<string>;
}

/**
 * What a loaded plug-in answers for `name`, or `undefined` when none does.
 * The caller decides what the product does without it.
 */
export function getCapability<K extends keyof PlatformCapabilities>(
  name: K,
): PlatformCapabilities[K] | undefined {
  return host.getCapability(name);
}
