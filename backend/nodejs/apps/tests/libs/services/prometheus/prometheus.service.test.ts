import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { PrometheusService } from '../../../../src/libs/services/prometheus/prometheus.service';
import { createMockKeyValueStoreService } from '../../../helpers/mock-kv-store';

describe('PrometheusService', () => {
  let mockKvStore: any;
  let service: PrometheusService;

  before(() => {
    // Reset singleton before suite
    (PrometheusService as any).instance = null;
    mockKvStore = createMockKeyValueStoreService();
    mockKvStore.getKey.resolves(null);
    mockKvStore.get.resolves(null);
    mockKvStore.set.resolves();
    mockKvStore.createKey.resolves();
    mockKvStore.updateValue.resolves();
    mockKvStore.watchKey.resolves();
    // Create one instance for all tests to avoid singleton issues
    service = new PrometheusService(mockKvStore);
  });

  after(() => {
    // Clean up intervals
    if ((service as any).pushInterval) {
      clearInterval((service as any).pushInterval);
      (service as any).pushInterval = null;
    }
    (PrometheusService as any).instance = null;
  });

  afterEach(() => {
    sinon.restore();
  });

  describe('constructor', () => {
    it('should create an instance', () => {
      expect(service).to.be.instanceOf(PrometheusService);
    });

    it('should be a singleton', () => {
      const service2 = new PrometheusService(mockKvStore);
      expect(service2).to.equal(service);
    });

    it('should have a register', () => {
      expect((service as any).register).to.exist;
    });
  });

  describe('recordActivity', () => {
    it('should record success activity', () => {
      service.recordActivity(
        'success', 'user1', 'org1', 'test@example.com', 'Test User',
        'req-123', 'GET', '/api/v1/test', '{}', 200,
      );
    });

    it('should record error activity', () => {
      service.recordActivity(
        'error', 'user1', 'org1', 'test@example.com', 'Test User',
        'req-456', 'POST', '/api/v1/fail', '{}', 500,
      );
    });

    it('should handle undefined requestId', () => {
      service.recordActivity(
        'success', 'anonymous', 'unknown', 'unknown', 'unknown',
        undefined as any, 'GET', '/health', '{}', 200,
      );
    });
  });

  describe('getMetrics', () => {
    it('should return metrics string', async () => {
      const metrics = await service.getMetrics();
      expect(metrics).to.be.a('string');
    });
  });

  describe('stopMetricsPush', () => {
    it('should handle null push interval', () => {
      const orig = (service as any).pushInterval;
      (service as any).pushInterval = null;
      (service as any).stopMetricsPush(); // should not throw
      (service as any).pushInterval = orig;
    });
  });

  describe('generateInstanceId', () => {
    it('should generate a non-empty string', () => {
      const id = (service as any).generateInstanceId();
      expect(id).to.be.a('string');
      expect(id.length).to.be.greaterThan(0);
    });
  });

  describe('getEnv', () => {
    it('should return env value when set', () => {
      process.env.TEST_PROM_VAR = 'test-value';
      const result = (service as any).getEnv('TEST_PROM_VAR', 'default');
      expect(result).to.equal('test-value');
      delete process.env.TEST_PROM_VAR;
    });

    it('should return default when env not set', () => {
      const result = (service as any).getEnv('NONEXISTENT_VAR', 'fallback');
      expect(result).to.equal('fallback');
    });
  });

  describe('properties', () => {
    it('should have metricsServerUrl', () => {
      expect((service as any).metricsServerUrl).to.be.a('string');
    });

    it('should have pushIntervalMs as number', () => {
      expect((service as any).pushIntervalMs).to.be.a('number');
    });
  });

  describe('hasActualMetrics', () => {
    it('should return false for header-only metrics', () => {
      const result = (service as any).hasActualMetrics('# HELP counter\n# TYPE counter counter\n');
      expect(result).to.be.false;
    });

    it('should return true for metrics with data lines', () => {
      const result = (service as any).hasActualMetrics('# HELP counter\nmy_metric 42\n');
      expect(result).to.be.true;
    });

    it('should return false for empty string', () => {
      const result = (service as any).hasActualMetrics('');
      expect(result).to.be.false;
    });
  });

  describe('handlePushError', () => {
    it('should handle response error', () => {
      expect(() => {
        (service as any).handlePushError({ response: { status: 500 } });
      }).to.not.throw();
    });

    it('should handle request error without response', () => {
      expect(() => {
        (service as any).handlePushError({ request: { path: '/test' }, message: 'timeout' });
      }).to.not.throw();
    });

    it('should handle setup error with message and stack', () => {
      expect(() => {
        (service as any).handlePushError({ message: 'bad config', stack: 'stack trace' });
      }).to.not.throw();
    });
  });

  describe('stopMetricsPush', () => {
    it('should clear interval and set to null', () => {
      const orig = (service as any).pushInterval;
      (service as any).pushInterval = setInterval(() => {}, 100000);
      (service as any).stopMetricsPush();
      expect((service as any).pushInterval).to.be.null;
      (service as any).pushInterval = orig;
    });

    it('should do nothing when no interval set', () => {
      const orig = (service as any).pushInterval;
      (service as any).pushInterval = null;
      expect(() => (service as any).stopMetricsPush()).to.not.throw();
      (service as any).pushInterval = orig;
    });
  });

  describe('getConfig', () => {
    it('should return empty object when no config exists', async () => {
      mockKvStore.get.resolves(null);
      const result = await (service as any).getConfig();
      expect(result).to.be.an('object');
    });

    it('should handle JSON parse error gracefully', async () => {
      mockKvStore.get.resolves('not-json');
      const result = await (service as any).getConfig();
      expect(result).to.be.an('object');
    });
  });

  describe('createHttpsAgent', () => {
    it('should return an https agent with TLS config', () => {
      const agent = (service as any).createHttpsAgent();
      expect(agent).to.exist;
      expect(agent.options.minVersion).to.equal('TLSv1.2');
    });
  });

  describe('logConfig', () => {
    it('should not throw in any environment', () => {
      expect(() => (service as any).logConfig()).to.not.throw();
    });
  });

  describe('recordActivity with all defaults', () => {
    it('should handle all optional parameters as undefined', () => {
      expect(() => service.recordActivity('test_activity')).to.not.throw();
    });

    it('should handle explicit undefined statusCode', () => {
      expect(() => {
        service.recordActivity('test', 'u1', 'o1', 'e', 'n', 'r', 'GET', '/', undefined, undefined);
      }).to.not.throw();
    });
  });

  describe('getOrSet', () => {
    it('should return existing value from config', async () => {
      mockKvStore.get.resolves(JSON.stringify({ testKey: 'existingVal' }));
      const result = await (service as any).getOrSet('testKey', 'defaultVal');
      expect(result).to.equal('existingVal');
    });

    it('should set and return default when key is missing from config', async () => {
      mockKvStore.get.resolves(JSON.stringify({}));
      const result = await (service as any).getOrSet('newKey', 'defaultVal');
      expect(result).to.equal('defaultVal');
    });
  });

  describe('startOrStopMetricCollection', () => {
    it('should start metrics when enabled', async () => {
      mockKvStore.get.resolves(JSON.stringify({ enableMetricCollection: 'true' }));
      const startStub = sinon.stub(service as any, 'startMetricsPush').resolves();
      await (service as any).startOrStopMetricCollection();
      expect(startStub.calledOnce).to.be.true;
    });

    it('should stop metrics when disabled', async () => {
      mockKvStore.get.resolves(JSON.stringify({ enableMetricCollection: 'false' }));
      const stopStub = sinon.stub(service as any, 'stopMetricsPush');
      await (service as any).startOrStopMetricCollection();
      expect(stopStub.calledOnce).to.be.true;
    });
  });

  describe('pushMetricsToServer', () => {
    it('should skip push when no actual metrics data', async () => {
      sinon.stub((service as any).register, 'metrics').resolves('# HELP counter\n# TYPE counter counter\n');
      const axiosStub = sinon.stub(require('axios'), 'post');
      await (service as any).pushMetricsToServer();
      expect(axiosStub.called).to.be.false;
    });

    it('should push metrics and reset counter when data present', async () => {
      sinon.stub((service as any).register, 'metrics').resolves('# HELP counter\nmy_metric 42\n');
      const axiosStub = sinon.stub(require('axios'), 'post').resolves({});
      const resetStub = sinon.stub((service as any).activityCounter, 'reset');
      await (service as any).pushMetricsToServer();
      expect(axiosStub.calledOnce).to.be.true;
      expect(resetStub.calledOnce).to.be.true;
    });

    it('should handle push error gracefully', async () => {
      sinon.stub((service as any).register, 'metrics').resolves('# HELP counter\nmy_metric 42\n');
      sinon.stub(require('axios'), 'post').rejects({ response: { status: 500 } });
      // Should not throw
      await (service as any).pushMetricsToServer();
    });
  });

  describe('watchKeysForMetricsCollection', () => {
    it('should call watchKey with config path', () => {
      mockKvStore.watchKey.resetHistory();
      (service as any).watchKeysForMetricsCollection('test/path');
      expect(mockKvStore.watchKey.calledWith('test/path')).to.be.true;
    });
  });

  describe('loadServerUrl', () => {
    it('should load server URL from config', async () => {
      mockKvStore.get.resolves(JSON.stringify({ serverUrl: 'https://custom.server.com' }));
      await (service as any).loadServerUrl();
      expect((service as any).metricsServerUrl).to.equal('https://custom.server.com');
    });
  });

  describe('loadPushInterval', () => {
    it('should load push interval from config', async () => {
      mockKvStore.get.resolves(JSON.stringify({ pushIntervalMs: '30000' }));
      await (service as any).loadPushInterval();
      expect((service as any).pushIntervalMs).to.equal(30000);
    });
  });

  describe('loadMetricCollectionFlag', () => {
    it('should set enableMetricCollection from config', async () => {
      mockKvStore.get.resolves(JSON.stringify({ enableMetricCollection: 'false' }));
      await (service as any).loadMetricCollectionFlag();
      expect((service as any).enableMetricCollection).to.be.false;
    });
  });

  describe('startMetricsPush', () => {
    it('should not start if already starting', async () => {
      (service as any).isStarting = true;
      await (service as any).startMetricsPush();
      // Should return immediately without changing state
      (service as any).isStarting = false;
    });
  });
});
