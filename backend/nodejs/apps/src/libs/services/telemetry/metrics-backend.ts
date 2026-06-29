import * as promClient from 'prom-client';

export type MetricLabels = Record<string, string | number>;

export interface CounterHandle {
  inc(labels?: MetricLabels, value?: number): void;
}

export interface GaugeHandle {
  set(labels: MetricLabels, value: number): void;
  /** Drop all current series (used by current-state gauges before a refresh). */
  reset(): void;
}

export interface HistogramHandle {
  observe(labels: MetricLabels, value: number): void;
}

export interface MetricDefinition {
  name: string;
  help: string;
  labelNames: string[];
}

export interface HistogramDefinition extends MetricDefinition {
  buckets: number[];
}

/** A metrics registry capable of creating instruments and serializing them. */
export interface MetricsBackend {
  createCounter(def: MetricDefinition): CounterHandle;
  createGauge(def: MetricDefinition): GaugeHandle;
  createHistogram(def: HistogramDefinition): HistogramHandle;
  /** Serialize all registered metrics in the backend's exposition format. */
  serialize(): Promise<string>;
}

/** prom-client implementation of {@link MetricsBackend}. */
export class PrometheusBackend implements MetricsBackend {
  private readonly registry = new promClient.Registry();

  createCounter(def: MetricDefinition): CounterHandle {
    const counter = new promClient.Counter({
      name: def.name,
      help: def.help,
      labelNames: def.labelNames,
      registers: [this.registry],
    });
    return {
      inc: (labels, value) => {
        if (labels) {
          counter.inc(labels, value ?? 1);
        } else {
          counter.inc(value ?? 1);
        }
      },
    };
  }

  createGauge(def: MetricDefinition): GaugeHandle {
    const gauge = new promClient.Gauge({
      name: def.name,
      help: def.help,
      labelNames: def.labelNames,
      registers: [this.registry],
    });
    return {
      set: (labels, value) => {
        gauge.set(labels, value);
      },
      reset: () => {
        gauge.reset();
      },
    };
  }

  createHistogram(def: HistogramDefinition): HistogramHandle {
    const histogram = new promClient.Histogram({
      name: def.name,
      help: def.help,
      labelNames: def.labelNames,
      buckets: def.buckets,
      registers: [this.registry],
    });
    return {
      observe: (labels, value) => {
        histogram.observe(labels, value);
      },
    };
  }

  serialize(): Promise<string> {
    return this.registry.metrics();
  }
}

export const metricsBackend: MetricsBackend = new PrometheusBackend();
