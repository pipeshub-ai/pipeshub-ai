import { getCapability } from '../plugins/capabilities';
import type {
  HistogramDefinition,
  MetricDefinition,
  MetricLabels,
  MetricsCapability,
} from '../plugins/capabilities';

/**
 * Operational metrics for application code, on whatever registry a plug-in provides (the `metrics` capability).
 *
 * Instruments are declared where the code that counts lives, usually at module load, before plug-ins load: each is
 * created on the provided registry at its first use. A build without such a plug-in counts nothing, and
 * `serialize()` answers an empty registry.
 */

type Counter = ReturnType<MetricsCapability['createCounter']>;
type Gauge = ReturnType<MetricsCapability['createGauge']>;
type Histogram = ReturnType<MetricsCapability['createHistogram']>;

function lazily<T>(create: (registry: MetricsCapability) => T): () => T | undefined {
  let made: T | undefined;
  return () => {
    if (made !== undefined) return made;
    const registry = getCapability('metrics');
    if (registry === undefined) return undefined;
    made = create(registry);
    return made;
  };
}

export const opsMetrics = {
  createCounter(def: MetricDefinition): Counter {
    const real = lazily((r) => r.createCounter(def));
    return {
      inc: (labels?: MetricLabels, value?: number) => real()?.inc(labels, value),
    };
  },
  createGauge(def: MetricDefinition): Gauge {
    const real = lazily((r) => r.createGauge(def));
    return {
      set: (labels: MetricLabels, value: number) => real()?.set(labels, value),
      reset: () => real()?.reset(),
    };
  },
  createHistogram(def: HistogramDefinition): Histogram {
    const real = lazily((r) => r.createHistogram(def));
    return {
      observe: (labels: MetricLabels, value: number) =>
        real()?.observe(labels, value),
    };
  },
  /** The provided registry in the Prometheus text format; empty without one. */
  serialize(): Promise<string> {
    return getCapability('metrics')?.serialize() ?? Promise.resolve('');
  },
};

export type { HistogramDefinition, MetricDefinition, MetricLabels };
