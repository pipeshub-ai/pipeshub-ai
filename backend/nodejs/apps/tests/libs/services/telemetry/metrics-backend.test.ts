import { expect } from 'chai';
import { PrometheusBackend } from '../../../../src/libs/services/telemetry/metrics-backend';

describe('telemetry metrics-backend (PrometheusBackend)', () => {
  let backend: PrometheusBackend;

  beforeEach(() => {
    backend = new PrometheusBackend();
  });

  describe('createCounter', () => {
    it('should increment with labels and default value 1', async () => {
      const counter = backend.createCounter({
        name: 'test_counter_total',
        help: 'test',
        labelNames: ['kind'],
      });

      counter.inc({ kind: 'a' });
      counter.inc({ kind: 'a' }, 2);

      const text = await backend.serialize();
      expect(text).to.include('test_counter_total{kind="a"} 3');
    });

    it('should increment without labels', async () => {
      const counter = backend.createCounter({
        name: 'test_plain_total',
        help: 'test',
        labelNames: [],
      });

      counter.inc();
      counter.inc(undefined, 4);

      const text = await backend.serialize();
      expect(text).to.include('test_plain_total 5');
    });
  });

  describe('createGauge', () => {
    it('should set values per label set', async () => {
      const gauge = backend.createGauge({
        name: 'test_gauge',
        help: 'test',
        labelNames: ['org'],
      });

      gauge.set({ org: 'o1' }, 7);
      gauge.set({ org: 'o2' }, 0);

      const text = await backend.serialize();
      expect(text).to.include('test_gauge{org="o1"} 7');
      expect(text).to.include('test_gauge{org="o2"} 0');
    });

    it('should drop all series on reset (stale series must not survive a refresh)', async () => {
      const gauge = backend.createGauge({
        name: 'test_reset_gauge',
        help: 'test',
        labelNames: ['org'],
      });

      gauge.set({ org: 'stale' }, 1);
      gauge.reset();
      gauge.set({ org: 'fresh' }, 2);

      const text = await backend.serialize();
      expect(text).to.not.include('org="stale"');
      expect(text).to.include('test_reset_gauge{org="fresh"} 2');
    });
  });

  describe('createHistogram', () => {
    it('should observe values into the configured buckets', async () => {
      const histogram = backend.createHistogram({
        name: 'test_duration_seconds',
        help: 'test',
        labelNames: ['route'],
        buckets: [0.1, 1, 10],
      });

      histogram.observe({ route: '/x' }, 0.05);
      histogram.observe({ route: '/x' }, 5);

      const text = await backend.serialize();
      expect(text).to.include(
        'test_duration_seconds_bucket{le="0.1",route="/x"} 1',
      );
      expect(text).to.include(
        'test_duration_seconds_bucket{le="10",route="/x"} 2',
      );
      expect(text).to.include('test_duration_seconds_count{route="/x"} 2');
      expect(text).to.include('test_duration_seconds_sum{route="/x"} 5.05');
    });
  });

  describe('serialize', () => {
    it('should use an isolated registry per backend instance', async () => {
      const other = new PrometheusBackend();
      const counter = backend.createCounter({
        name: 'test_isolated_total',
        help: 'test',
        labelNames: [],
      });
      counter.inc();

      expect(await backend.serialize()).to.include('test_isolated_total 1');
      expect(await other.serialize()).to.not.include('test_isolated_total');
    });
  });
});
