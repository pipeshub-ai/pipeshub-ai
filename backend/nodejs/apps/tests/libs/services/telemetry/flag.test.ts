import { expect } from 'chai';
import sinon from 'sinon';
import {
  isTelemetryEnabled,
  TELEMETRY_COLLECTION_FLAG,
} from '../../../../src/libs/services/telemetry/flag';
import * as flagModule from '../../../../src/libs/services/telemetry/flag';
import { TELEMETRY_DEFAULT_ENABLED } from '../../../../src/edition';
import {
  NoopBackend,
  PrometheusBackend,
  metricsBackend,
} from '../../../../src/libs/services/telemetry/metrics-backend';
import {
  eventBuffer,
  recordEvent,
} from '../../../../src/libs/services/telemetry/event-buffer';

describe('telemetry flag', () => {
  let previous: string | undefined;

  beforeEach(() => {
    previous = process.env[TELEMETRY_COLLECTION_FLAG];
  });

  afterEach(() => {
    if (previous == null) {
      delete process.env[TELEMETRY_COLLECTION_FLAG];
    } else {
      process.env[TELEMETRY_COLLECTION_FLAG] = previous;
    }
    sinon.restore();
    eventBuffer.drain();
  });

  describe('isTelemetryEnabled', () => {
    it('uses the edition default when the variable is unset', () => {
      delete process.env[TELEMETRY_COLLECTION_FLAG];
      expect(isTelemetryEnabled()).to.equal(TELEMETRY_DEFAULT_ENABLED);
    });

    for (const value of ['', '   ']) {
      it(`uses the edition default for "${value}"`, () => {
        process.env[TELEMETRY_COLLECTION_FLAG] = value;
        expect(isTelemetryEnabled()).to.equal(TELEMETRY_DEFAULT_ENABLED);
      });
    }

    for (const value of ['true', 'TRUE', ' True ']) {
      it(`is on for "${value}"`, () => {
        process.env[TELEMETRY_COLLECTION_FLAG] = value;
        expect(isTelemetryEnabled()).to.be.true;
      });
    }

    for (const value of ['false', '0', '1', 'yes']) {
      it(`is off for "${value}"`, () => {
        process.env[TELEMETRY_COLLECTION_FLAG] = value;
        expect(isTelemetryEnabled()).to.be.false;
      });
    }
  });

  describe('metrics backend selection', () => {
    it('uses the real backend when the suite bootstrap turned telemetry on', () => {
      expect(metricsBackend).to.be.instanceOf(PrometheusBackend);
    });

    it('NoopBackend accepts every call and serializes nothing', async () => {
      const backend = new NoopBackend();
      const counter = backend.createCounter({
        name: 'n_counter',
        help: 'h',
        labelNames: ['k'],
      });
      const gauge = backend.createGauge({
        name: 'n_gauge',
        help: 'h',
        labelNames: ['k'],
      });
      const histogram = backend.createHistogram({
        name: 'n_hist',
        help: 'h',
        labelNames: ['k'],
        buckets: [1],
      });

      counter.inc({ k: 'x' });
      counter.inc();
      gauge.set({ k: 'x' }, 1);
      gauge.reset();
      histogram.observe({ k: 'x' }, 0.5);
      backend.reset();

      expect(await backend.serialize()).to.equal('');
    });
  });

  describe('recordEvent', () => {
    it('drops events while telemetry is off', () => {
      sinon.stub(flagModule, 'isTelemetryEnabled').returns(false);

      recordEvent('user_signed_in', { org: 'o1' });

      expect(eventBuffer.size()).to.equal(0);
    });

    it('buffers events while telemetry is on', () => {
      sinon.stub(flagModule, 'isTelemetryEnabled').returns(true);

      recordEvent('user_signed_in', { org: 'o1' });

      expect(eventBuffer.drain().map((e) => e.event)).to.deep.equal([
        'user_signed_in',
      ]);
    });
  });
});
