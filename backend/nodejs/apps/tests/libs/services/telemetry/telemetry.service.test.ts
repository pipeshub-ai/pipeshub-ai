import { expect } from 'chai';
import sinon from 'sinon';
import axios from 'axios';
import {
  TelemetryService,
  startTelemetry,
} from '../../../../src/libs/services/telemetry/telemetry.service';
import * as installMetricsModule from '../../../../src/libs/services/telemetry/modules/install-metrics';
import * as cmConfigModule from '../../../../src/modules/configuration_manager/config/config';
import { configPaths } from '../../../../src/modules/configuration_manager/paths/paths';
import { recordEvent, eventBuffer } from '../../../../src/libs/services/telemetry/event-buffer';
import { StoreType } from '../../../../src/libs/keyValueStore/constants/KeyValueStoreType';

const VALID_URL = 'http://localhost:3031/collect-metrics';

// Config stored under /services/metricsCollection. Collection is disabled by
// default so constructing the service never starts the real push interval.
function storedConfig(overrides: Record<string, string> = {}): string {
  return JSON.stringify({
    serverUrl: VALID_URL,
    apiKey: 'test-api-key',
    installId: 'install-1234',
    appVersion: '9.9.9',
    pushIntervalMs: '60000',
    enableMetricCollection: 'false',
    ...overrides,
  });
}

function mockKvStore(raw: string | null = storedConfig()) {
  return {
    get: sinon.stub().resolves(raw),
    set: sinon.stub().resolves(undefined),
    watchKey: sinon.stub().resolves(undefined),
  };
}

const flushAsync = () => new Promise<void>((resolve) => setImmediate(resolve));

describe('TelemetryService', () => {
  let sandbox: sinon.SinonSandbox;

  beforeEach(() => {
    sandbox = sinon.createSandbox();
    sandbox.stub(cmConfigModule, 'loadConfigurationManagerConfig').returns({
      storeType: StoreType.Redis,
      storeConfig: { host: 'http://localhost', port: 2379, dialTimeout: 2000 },
      redisConfig: {
        host: 'localhost',
        port: 6379,
        password: undefined,
        tls: false,
        db: 0,
        keyPrefix: 'pipeshub:kv:',
      },
      secretKey: 'a'.repeat(64),
      algorithm: 'aes-256-gcm',
    } as any);
    eventBuffer.drain();
  });

  afterEach(() => {
    const instance = TelemetryService.current();
    if (instance) {
      (instance as any).stopMetricsPush();
    }
    (TelemetryService as any).instance = undefined;
    eventBuffer.drain();
    sandbox.restore();
  });

  describe('singleton behaviour', () => {
    it('should reuse the existing instance on repeated construction', async () => {
      const kv = mockKvStore();
      const first = new TelemetryService(kv as any);
      const second = new TelemetryService(kv as any);

      expect(second).to.equal(first);
      expect(TelemetryService.current()).to.equal(first);
    });

    it('startTelemetry should create the singleton', async () => {
      startTelemetry(mockKvStore() as any);
      await flushAsync();

      expect(TelemetryService.current()).to.be.instanceOf(TelemetryService);
    });
  });

  describe('config loading', () => {
    it('should load values from the stored metrics config', async () => {
      const kv = mockKvStore();
      const svc = new TelemetryService(kv as any);
      await flushAsync();

      expect((svc as any).metricsServerUrl).to.equal(VALID_URL);
      expect((svc as any).apiKey).to.equal('test-api-key');
      expect((svc as any).instanceId).to.equal('install-1234');
      expect((svc as any).appVersion).to.equal('9.9.9');
      expect((svc as any).pushIntervalMs).to.equal(60000);
      expect((svc as any).enableMetricCollection).to.be.false;
      // All keys present — nothing should be written back
      expect(kv.set.called).to.be.false;
    });

    it('should watch the metrics config key for changes', async () => {
      const kv = mockKvStore();
      new TelemetryService(kv as any);
      await flushAsync();

      expect(kv.watchKey.calledWith(configPaths.metricsCollection)).to.be.true;
    });

    it('should generate and persist missing apiKey/installId', async () => {
      const kv = mockKvStore(
        JSON.stringify({
          serverUrl: VALID_URL,
          appVersion: '1.0.0',
          pushIntervalMs: '60000',
          enableMetricCollection: 'false',
        }),
      );
      const svc = new TelemetryService(kv as any);
      await flushAsync();

      expect((svc as any).apiKey).to.have.length.greaterThan(0);
      // installId is a uuid
      expect((svc as any).instanceId).to.match(
        /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
      );
      expect(kv.set.called).to.be.true;
    });

    it('should return an empty config when the store is empty or invalid JSON', async () => {
      const svc = new TelemetryService(mockKvStore() as any);
      await flushAsync();

      const kv = (svc as any).kvStore;
      kv.get.resolves(null);
      expect(await (svc as any).getConfig()).to.deep.equal({});

      kv.get.resolves('not-json-at-all');
      expect(await (svc as any).getConfig()).to.deep.equal({});
    });
  });

  describe('deriveEventsUrl', () => {
    let svc: TelemetryService;

    beforeEach(async () => {
      svc = new TelemetryService(mockKvStore() as any);
      await flushAsync();
    });

    it('should swap collect-metrics for collect-events', () => {
      (svc as any).metricsServerUrl = 'https://collector.example.com/collect-metrics';
      expect((svc as any).deriveEventsUrl()).to.equal(
        'https://collector.example.com/collect-events',
      );
    });

    it('should handle a trailing slash', () => {
      (svc as any).metricsServerUrl = 'https://collector.example.com/collect-metrics/';
      expect((svc as any).deriveEventsUrl()).to.equal(
        'https://collector.example.com/collect-events',
      );
    });

    it('should fall back to /collect-events for URLs without collect-metrics', () => {
      (svc as any).metricsServerUrl = 'https://collector.example.com/ingest';
      expect((svc as any).deriveEventsUrl()).to.equal(
        'https://collector.example.com/collect-events',
      );
    });
  });

  describe('hasActualMetrics / isServerUrlValid', () => {
    let svc: TelemetryService;

    beforeEach(async () => {
      svc = new TelemetryService(mockKvStore() as any);
      await flushAsync();
    });

    it('should treat comment-only exposition text as having no metrics', () => {
      const commentsOnly = '# HELP x y\n# TYPE x counter\n\n';
      expect((svc as any).hasActualMetrics(commentsOnly)).to.be.false;
    });

    it('should detect sample lines', () => {
      const withSample = '# HELP x y\n# TYPE x counter\nx{a="b"} 1\n';
      expect((svc as any).hasActualMetrics(withSample)).to.be.true;
    });

    it('should reject empty and malformed server URLs', () => {
      (svc as any).metricsServerUrl = '';
      expect((svc as any).isServerUrlValid()).to.be.false;

      (svc as any).metricsServerUrl = 'not a url';
      expect((svc as any).isServerUrlValid()).to.be.false;

      (svc as any).metricsServerUrl = VALID_URL;
      expect((svc as any).isServerUrlValid()).to.be.true;
    });
  });

  describe('buildHeaders', () => {
    it('should include bearer auth, metrics version, and a request id', async () => {
      const svc = new TelemetryService(mockKvStore() as any);
      await flushAsync();

      const headers = (svc as any).buildHeaders();
      expect(headers['Authorization']).to.equal('Bearer test-api-key');
      expect(headers['X-Metrics-Version']).to.equal('2');
      expect(headers['Content-Type']).to.equal('application/json');
    });
  });

  describe('flush', () => {
    it('should do nothing when the server URL is invalid', async () => {
      const postStub = sandbox.stub(axios, 'post').resolves({ data: {} });
      const svc = new TelemetryService(mockKvStore() as any);
      await flushAsync();

      (svc as any).metricsServerUrl = '';
      recordEvent('opt_out');
      await svc.flush();

      expect(postStub.called).to.be.false;
    });

    it('should ship buffered events to the events endpoint', async () => {
      const postStub = sandbox.stub(axios, 'post').resolves({ data: {} });
      const svc = new TelemetryService(mockKvStore() as any);
      await flushAsync();

      recordEvent('consent_changed', { enabled: false });
      await svc.flush();

      const eventsCall = postStub
        .getCalls()
        .find((c) => String(c.args[0]).includes('collect-events'));
      expect(eventsCall, 'expected a POST to collect-events').to.exist;
      const payload = eventsCall!.args[1] as any;
      expect(payload.instanceId).to.equal('install-1234');
      expect(payload.events).to.have.length(1);
      expect(payload.events[0].event).to.equal('consent_changed');
    });

    it('should not ship when there are no buffered events', async () => {
      const postStub = sandbox.stub(axios, 'post').resolves({ data: {} });
      const svc = new TelemetryService(mockKvStore() as any);
      await flushAsync();

      await svc.flush();

      const eventsCall = postStub
        .getCalls()
        .find((c) => String(c.args[0]).includes('collect-events'));
      expect(eventsCall).to.be.undefined;
    });

    it('should survive a collector error without throwing', async () => {
      sandbox.stub(axios, 'post').rejects(new Error('ECONNREFUSED'));
      const svc = new TelemetryService(mockKvStore() as any);
      await flushAsync();

      recordEvent('login');
      await svc.flush(); // must not throw
    });
  });

  describe('refreshInstallInfo', () => {
    it('should read deployment config and set install info', async () => {
      const setInstallInfoStub = sandbox.stub(installMetricsModule, 'setInstallInfo');
      const kv = mockKvStore();
      kv.get
        .withArgs(configPaths.deployment)
        .resolves(
          JSON.stringify({
            dataStoreType: 'arangodb',
            vectorDbType: 'qdrant',
            messageBrokerType: 'kafka',
            kvStoreType: 'redis',
          }),
        );
      const svc = new TelemetryService(kv as any);
      await flushAsync();

      await (svc as any).refreshInstallInfo();

      expect(setInstallInfoStub.calledOnce).to.be.true;
      expect(setInstallInfoStub.firstCall.args[0]).to.deep.equal({
        graph_db: 'arangodb',
        vector_db: 'qdrant',
        message_broker: 'kafka',
        kv_store: 'redis',
      });
    });

    it('should handle a double-encoded deployment config', async () => {
      const setInstallInfoStub = sandbox.stub(installMetricsModule, 'setInstallInfo');
      const kv = mockKvStore();
      kv.get
        .withArgs(configPaths.deployment)
        .resolves(JSON.stringify(JSON.stringify({ dataStoreType: 'neo4j' })));
      const svc = new TelemetryService(kv as any);
      await flushAsync();

      await (svc as any).refreshInstallInfo();

      expect(setInstallInfoStub.firstCall.args[0].graph_db).to.equal('neo4j');
    });
  });

  describe('refreshInfraHealth', () => {
    it('should map health endpoint services to up/down rows', async () => {
      const setInfraStub = sandbox.stub(installMetricsModule, 'setInfraServiceUp');
      sandbox.stub(axios, 'get').resolves({
        data: {
          services: { mongodb: 'healthy', kafka: 'unhealthy' },
        },
      });
      const svc = new TelemetryService(mockKvStore() as any);
      await flushAsync();

      await (svc as any).refreshInfraHealth();

      expect(setInfraStub.calledOnce).to.be.true;
      expect(setInfraStub.firstCall.args[0]).to.have.deep.members([
        { service: 'mongodb', healthy: true },
        { service: 'kafka', healthy: false },
      ]);
    });

    it('should throttle health probes to once per window', async () => {
      const getStub = sandbox.stub(axios, 'get').resolves({ data: { services: {} } });
      const svc = new TelemetryService(mockKvStore() as any);
      await flushAsync();

      await (svc as any).refreshInfraHealth();
      await (svc as any).refreshInfraHealth();

      expect(getStub.calledOnce).to.be.true;
    });
  });

  describe('push loop control', () => {
    it('should start the interval when collection is enabled and stop it when disabled', async () => {
      const kv = mockKvStore();
      const svc = new TelemetryService(kv as any);
      await flushAsync();
      expect((svc as any).pushInterval).to.be.null;

      kv.get.resolves(storedConfig({ enableMetricCollection: 'true' }));
      await (svc as any).startOrStopMetricCollection();
      expect((svc as any).pushInterval).to.not.be.null;

      kv.get.resolves(storedConfig({ enableMetricCollection: 'false' }));
      await (svc as any).startOrStopMetricCollection();
      expect((svc as any).pushInterval).to.be.null;
    });
  });
});
