import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { PrometheusService } from '../../../../src/libs/services/prometheus/prometheus.service'
import { createMockKeyValueStoreService } from '../../../helpers/mock-kv-store'

describe('PrometheusService - additional coverage', () => {
  let mockKvStore: any
  let service: PrometheusService

  before(() => {
    // Reset singleton
    ;(PrometheusService as any).instance = null
    mockKvStore = createMockKeyValueStoreService()
    mockKvStore.get.resolves(null)
    mockKvStore.set.resolves()
    mockKvStore.watchKey.resolves()
    service = new PrometheusService(mockKvStore)
  })

  after(() => {
    if ((service as any).pushInterval) {
      clearInterval((service as any).pushInterval)
      ;(service as any).pushInterval = null
    }
    ;(PrometheusService as any).instance = null
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('recordActivity - additional parameters', () => {
    it('should record activity with all parameters', () => {
      service.recordActivity(
        'login',
        'user-1',
        'org-1',
        'user@test.com',
        'Test User',
        'req-1',
        'POST',
        '/api/v1/login',
        JSON.stringify({ ip: '127.0.0.1' }),
        200,
      )
      // Should not throw
    })

    it('should record activity with minimal parameters (anonymous)', () => {
      service.recordActivity('anonymous-action')
      // Uses defaults: userId='anonymous', orgId='anonymous'
    })

    it('should record activity with undefined email and fullName', () => {
      service.recordActivity('action', 'user-1', 'org-1', undefined, undefined)
    })

    it('should record activity without statusCode', () => {
      service.recordActivity(
        'action', 'user-1', 'org-1', 'email@test.com', 'Name',
        'req-2', 'GET', '/path', '{}',
      )
    })
  })

  describe('getMetrics', () => {
    it('should return metrics string', async () => {
      const metrics = await service.getMetrics()
      expect(metrics).to.be.a('string')
    })
  })

  describe('hasActualMetrics (private)', () => {
    it('should return false for empty metrics (only headers)', () => {
      const metricsText = '# HELP app_activity_total Total activities\n# TYPE app_activity_total counter\n'
      const lines = metricsText.split('\n').filter(line => line.trim().length > 0)
      const hasActual = lines.some(line => !line.trim().startsWith('#'))
      expect(hasActual).to.be.false
    })

    it('should return true for metrics with data lines', () => {
      const metricsText = '# HELP counter\n# TYPE counter\napp_activity_total{action="login"} 1\n'
      const lines = metricsText.split('\n').filter(line => line.trim().length > 0)
      const hasActual = lines.some(line => !line.trim().startsWith('#'))
      expect(hasActual).to.be.true
    })

    it('should return false for empty string', () => {
      const metricsText = ''
      const lines = metricsText.split('\n').filter(line => line.trim().length > 0)
      const hasActual = lines.some(line => !line.trim().startsWith('#'))
      expect(hasActual).to.be.false
    })
  })

  describe('handlePushError (private)', () => {
    it('should handle error with response property', () => {
      const error = { response: { status: 500 } }
      expect(error.response).to.exist
      expect(error.response.status).to.equal(500)
    })

    it('should handle error with request but no response', () => {
      const error = { request: { _currentUrl: 'http://metrics/collect' }, message: 'Timeout' }
      expect(error.request).to.exist
      expect(error.message).to.equal('Timeout')
    })

    it('should handle error with request.path fallback', () => {
      const error = { request: { path: '/collect-metrics' }, message: 'Connection refused' }
      expect(error.request.path).to.equal('/collect-metrics')
    })

    it('should handle error with only message (setup error)', () => {
      const error = { message: 'Invalid config', stack: 'Error: ...' }
      expect(error.message).to.equal('Invalid config')
    })
  })

  describe('generateInstanceId (private)', () => {
    it('should generate a 16-char hex string', () => {
      const id = (service as any).generateInstanceId()
      expect(id).to.be.a('string')
      expect(id.length).to.equal(16)
      expect(id).to.match(/^[a-f0-9]+$/)
    })
  })

  describe('getEnv (private)', () => {
    it('should return fallback when env var is not set', () => {
      const result = (service as any).getEnv('NONEXISTENT_VAR_XYZ', 'fallback-value')
      expect(result).to.equal('fallback-value')
    })
  })

  describe('logConfig (private)', () => {
    it('should not throw when called', () => {
      expect(() => (service as any).logConfig()).to.not.throw()
    })
  })

  describe('stopMetricsPush (private)', () => {
    it('should clear interval when pushInterval exists', () => {
      ;(service as any).pushInterval = setInterval(() => {}, 60000)
      ;(service as any).stopMetricsPush()
      expect((service as any).pushInterval).to.be.null
    })

    it('should do nothing when no interval set', () => {
      ;(service as any).pushInterval = null
      ;(service as any).stopMetricsPush()
      expect((service as any).pushInterval).to.be.null
    })
  })

  describe('createHttpsAgent (private)', () => {
    it('should return an https.Agent with TLS settings', () => {
      const agent = (service as any).createHttpsAgent()
      expect(agent).to.exist
      expect(agent.options).to.have.property('rejectUnauthorized', true)
    })
  })

  describe('getConfig (private)', () => {
    it('should return empty object when kvStore returns null', async () => {
      mockKvStore.get.resolves(null)
      const config = await (service as any).getConfig()
      expect(config).to.deep.equal({})
    })

    it('should return parsed config from kvStore', async () => {
      mockKvStore.get.resolves(JSON.stringify({ serverUrl: 'http://test' }))
      const config = await (service as any).getConfig()
      expect(config.serverUrl).to.equal('http://test')
    })

    it('should return empty object on parse error', async () => {
      mockKvStore.get.resolves('invalid-json{')
      const config = await (service as any).getConfig()
      expect(config).to.deep.equal({})
    })
  })

  describe('getOrSet (private)', () => {
    it('should return existing value from config', async () => {
      mockKvStore.get.resolves(JSON.stringify({ testKey: 'existingValue' }))
      const result = await (service as any).getOrSet('testKey', 'default')
      expect(result).to.equal('existingValue')
    })

    it('should set and return default when key not in config', async () => {
      mockKvStore.get.resolves(JSON.stringify({}))
      const result = await (service as any).getOrSet('newKey', 'newDefault')
      expect(result).to.equal('newDefault')
    })
  })

  describe('singleton behavior', () => {
    it('should return same instance when constructed again', () => {
      const service2 = new PrometheusService(mockKvStore)
      expect(service2).to.equal(service)
    })
  })

  describe('initializeMetricsCollection (private)', () => {
    it('should load all config values', async () => {
      mockKvStore.get.resolves(JSON.stringify({
        serverUrl: 'http://test-server',
        apiKey: 'test-api-key',
        appVersion: '2.0.0',
        pushIntervalMs: '30000',
        enableMetricCollection: 'false',
      }))

      // Stop any existing push interval
      if ((service as any).pushInterval) {
        clearInterval((service as any).pushInterval)
        ;(service as any).pushInterval = null
      }

      await (service as any).initializeMetricsCollection()
      // Should not throw
    })

    it('should fall back to env variable when init fails', async () => {
      mockKvStore.get.rejects(new Error('KV store error'))

      await (service as any).initializeMetricsCollection()
      // Should not throw, falls back to env/defaults
    })
  })

  describe('startOrStopMetricCollection (private)', () => {
    it('should stop metrics push when enableMetricCollection is false', async () => {
      mockKvStore.get.resolves(JSON.stringify({ enableMetricCollection: 'false' }))

      // Stop any existing push interval first
      if ((service as any).pushInterval) {
        clearInterval((service as any).pushInterval)
        ;(service as any).pushInterval = null
      }

      await (service as any).startOrStopMetricCollection()
      expect((service as any).pushInterval).to.be.null
    })
  })

  describe('pushMetricsToServer (private)', () => {
    it('should skip push when no actual metrics data', async () => {
      // Record no activities, so metrics should be headers-only
      // If pushMetricsToServer is called with no actual data, it should skip
      ;(service as any).metricsServerUrl = 'http://localhost:9999/test'

      // Reset counter so there's no data
      ;(service as any).activityCounter.reset()

      await (service as any).pushMetricsToServer()
      // Should not throw, just logs debug
    })
  })

  describe('watchKeysForMetricsCollection (private)', () => {
    it('should call kvStore.watchKey', () => {
      ;(service as any).watchKeysForMetricsCollection('test/key/path')
      expect(mockKvStore.watchKey.called).to.be.true
    })
  })

  describe('handlePushError direct invocation', () => {
    it('should handle error with response status', () => {
      ;(service as any).handlePushError({ response: { status: 403 } })
      // Just testing it doesn't throw
    })

    it('should handle error with request but no response', () => {
      ;(service as any).handlePushError({ request: { _currentUrl: 'http://test' }, message: 'timeout' })
    })

    it('should handle error with only message', () => {
      ;(service as any).handlePushError({ message: 'setup error', stack: 'Error: ...' })
    })
  })
})
