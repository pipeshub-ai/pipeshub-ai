import { expect } from 'chai';
import sinon from 'sinon';
import * as flag from '../../../../src/libs/services/telemetry/flag';
import { PrometheusBackend } from '../../../../src/libs/services/telemetry/metrics-backend';
import { eventBuffer, recordEvent } from '../../../../src/libs/services/telemetry/event-buffer';
import { metricsMiddleware } from '../../../../src/libs/middlewares/telemetry.middleware';
import { TelemetryService } from '../../../../src/libs/services/telemetry/telemetry.service';
import * as cmConfig from '../../../../src/modules/configuration_manager/config/config';
import { startOrgMetricsRefresh } from '../../../../src/modules/user_management/services/metrics.refresh.service';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';

// Telemetry collects only when the deployment flag is on AND an admin switched
// Data Collection on: nothing may be recorded that the admin cannot see or stop.
describe('telemetry collects only with the flag and the admin consent', () => {
  // Private: restoring sinon's shared sandbox would undo stubs other test files make at load.
  const sandbox = sinon.createSandbox();
  afterEach(() => {
    sandbox.restore();
    flag.setCollectionConsent(true);
    eventBuffer.drain();
  });

  it('needs both the flag and the consent', () => {
    const saved = process.env.TELEMETRY_COLLECTION_FLAG;
    try {
      for (const [flagOn, consent, expected] of [
        ['true', true, true], ['true', false, false], ['false', true, false], ['false', false, false],
      ] as const) {
        process.env.TELEMETRY_COLLECTION_FLAG = flagOn;
        flag.setCollectionConsent(consent);
        expect(flag.isCollecting(), `flag=${flagOn} consent=${consent}`).to.equal(expected);
      }
    } finally {
      process.env.TELEMETRY_COLLECTION_FLAG = saved;
    }
  });

  it('records no metric while consent is off, and records again once it is on', async () => {
    const backend = new PrometheusBackend();
    const counter = backend.createCounter({ name: 'consent_test_total', help: 'h', labelNames: ['k'] });
    const gauge = backend.createGauge({ name: 'consent_test_gauge', help: 'h', labelNames: ['k'] });
    const histogram = backend.createHistogram({ name: 'consent_test_seconds', help: 'h', labelNames: ['k'], buckets: [1] });

    flag.setCollectionConsent(false);
    counter.inc({ k: 'a' });
    gauge.set({ k: 'a' }, 5);
    histogram.observe({ k: 'a' }, 0.5);
    expect(await backend.serialize()).not.to.match(/^consent_test\w* ?\{/m);

    flag.setCollectionConsent(true);
    counter.inc({ k: 'a' });
    expect(await backend.serialize()).to.match(/^consent_test_total\{k="a"\} 1$/m);
  });

  it('drops events while consent is off', () => {
    flag.setCollectionConsent(false);
    recordEvent('search_performed');
    expect(eventBuffer.size()).to.equal(0);
    flag.setCollectionConsent(true);
    recordEvent('search_performed');
    expect(eventBuffer.size()).to.equal(1);
  });

  it('lets requests through untouched while consent is off', () => {
    flag.setCollectionConsent(false);
    const req = {} as any;
    const res = { on: sandbox.stub() } as any;
    const next = sandbox.stub();
    metricsMiddleware()(req, res, next);
    expect(next.calledOnce).to.equal(true);
    expect(res.on.called).to.equal(false);
    expect(req.__metricsAttached).to.equal(undefined);
  });

  describe('the telemetry service', () => {
    const config = (consent: string) => JSON.stringify({
      serverUrl: 'http://localhost:3031/collect-metrics', apiKey: 'k', installId: 'i',
      appVersion: '1', pushIntervalMs: '60000', enableMetricCollection: consent,
    });
    const start = async (consent: string) => {
      sandbox.stub(cmConfig, 'loadConfigurationManagerConfig').returns({ secretKey: 'a'.repeat(64), algorithm: 'aes-256-gcm' } as any);
      const kv = { get: sandbox.stub().resolves(config(consent)), set: sandbox.stub().resolves(), watchKey: sandbox.stub().resolves() };
      new TelemetryService(kv as any);
      await new Promise<void>((resolve) => setImmediate(resolve));
    };
    afterEach(() => {
      (TelemetryService.current() as any)?.stopMetricsPush();
      (TelemetryService as any).instance = undefined;
    });

    it('stops collecting and discards what it held when the admin switches collection off', async () => {
      recordEvent('gathered_while_on');
      await start('false');
      expect(flag.isCollecting()).to.equal(false);
      expect(eventBuffer.size()).to.equal(0);
    });

    it('collects once the admin switches collection on', async () => {
      flag.setCollectionConsent(false);
      await start('true');
      expect(flag.isCollecting()).to.equal(true);
    });
  });

  it('runs no org metrics query while consent is off', async () => {
    flag.setCollectionConsent(false);
    const aggregate = sandbox.stub(Users, 'aggregate').resolves([] as any);
    clearInterval(startOrgMetricsRefresh({ warn: sandbox.stub() } as any, 60_000));
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(aggregate.called).to.equal(false);
  });
});
