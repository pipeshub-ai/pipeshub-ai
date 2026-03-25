import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { createHealthRouter } from '../../../../src/modules/tokens_manager/routes/health.routes'

describe('tokens_manager/routes/health.routes', () => {
  let mockRedis: any
  let mockKafka: any
  let mockMongo: any
  let mockKV: any
  let mockAppConfig: any
  let container: any
  let cmContainer: any
  let router: any

  beforeEach(() => {
    mockRedis = { get: sinon.stub().resolves(null) }
    mockKafka = { healthCheck: sinon.stub().resolves(true) }
    mockMongo = { healthCheck: sinon.stub().resolves(true) }
    mockKV = { healthCheck: sinon.stub().resolves(true) }
    mockAppConfig = {
      aiBackend: 'http://localhost:8000',
      connectorBackend: 'http://localhost:8088',
    }

    container = {
      get: sinon.stub().callsFake((key: string) => {
        if (key === 'RedisService') return mockRedis
        if (key === 'KafkaService') return mockKafka
        if (key === 'MongoService') return mockMongo
        if (key === 'AppConfig') return mockAppConfig
      }),
    }

    cmContainer = {
      get: sinon.stub().returns(mockKV),
    }

    router = createHealthRouter(container, cmContainer)
  })

  afterEach(() => {
    sinon.restore()
  })

  function findHandler(path: string, method: string) {
    const layer = router.stack.find(
      (l: any) => l.route && l.route.path === path && l.route.methods[method],
    )
    if (!layer) return null
    return layer.route.stack[layer.route.stack.length - 1].handle
  }

  function mockRes() {
    const res: any = {
      status: sinon.stub().returnsThis(),
      json: sinon.stub().returnsThis(),
    }
    return res
  }

  describe('createHealthRouter', () => {
    it('should create a router with health check routes', () => {
      expect(router).to.exist

      const routes = (router as any).stack.filter((r: any) => r.route)
      const paths = routes.map((r: any) => r.route.path)

      expect(paths).to.include('/')
      expect(paths).to.include('/services')
    })
  })

  describe('GET / - health check', () => {
    it('should return healthy status when all services are healthy', async () => {
      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      expect(res.status.calledWith(200)).to.be.true
      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('healthy')
      expect(jsonArg.services.redis).to.equal('healthy')
      expect(jsonArg.services.kafka).to.equal('healthy')
      expect(jsonArg.services.mongodb).to.equal('healthy')
      expect(jsonArg.services.KVStoreservice).to.equal('healthy')
      expect(jsonArg.timestamp).to.be.a('string')
    })

    it('should mark redis as unhealthy when redis throws', async () => {
      mockRedis.get.rejects(new Error('Redis connection failed'))
      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      expect(res.status.calledWith(200)).to.be.true
      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.redis).to.equal('unhealthy')
    })

    it('should mark kafka as unhealthy when kafka healthCheck throws', async () => {
      mockKafka.healthCheck.rejects(new Error('Kafka down'))
      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.kafka).to.equal('unhealthy')
    })

    it('should mark mongodb as unhealthy when mongo healthCheck returns false', async () => {
      mockMongo.healthCheck.resolves(false)
      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.mongodb).to.equal('unhealthy')
    })

    it('should mark mongodb as unhealthy when mongo healthCheck throws', async () => {
      mockMongo.healthCheck.rejects(new Error('Mongo down'))
      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.mongodb).to.equal('unhealthy')
    })

    it('should mark KVStoreservice as unhealthy when kv healthCheck returns false', async () => {
      mockKV.healthCheck.resolves(false)
      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.KVStoreservice).to.equal('unhealthy')
    })

    it('should mark KVStoreservice as unhealthy when kv healthCheck throws', async () => {
      mockKV.healthCheck.rejects(new Error('KV down'))
      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.KVStoreservice).to.equal('unhealthy')
    })

    it('should mark all services as unhealthy when all fail', async () => {
      mockRedis.get.rejects(new Error('Redis down'))
      mockKafka.healthCheck.rejects(new Error('Kafka down'))
      mockMongo.healthCheck.rejects(new Error('Mongo down'))
      mockKV.healthCheck.rejects(new Error('KV down'))

      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.redis).to.equal('unhealthy')
      expect(jsonArg.services.kafka).to.equal('unhealthy')
      expect(jsonArg.services.mongodb).to.equal('unhealthy')
      expect(jsonArg.services.KVStoreservice).to.equal('unhealthy')
    })
  })

  describe('GET /services - combined services health check', () => {
    let axiosModule: any

    beforeEach(() => {
      axiosModule = require('axios')
    })

    it('should have a handler for /services', () => {
      const handler = findHandler('/services', 'get')
      expect(handler).to.be.a('function')
    })

    it('should return healthy when both ai and connector services are healthy', async () => {
      sinon.stub(axiosModule, 'get').callsFake((url: string) => {
        return Promise.resolve({ status: 200, data: { status: 'healthy' } })
      })

      const handler = findHandler('/services', 'get')
      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('healthy')
      expect(jsonArg.services.query).to.equal('healthy')
      expect(jsonArg.services.connector).to.equal('healthy')
      expect(res.status.calledWith(200)).to.be.true
    })

    it('should return unhealthy with 503 when ai service is down', async () => {
      sinon.stub(axiosModule, 'get').callsFake((url: string) => {
        if (url.includes('8000')) {
          return Promise.reject(new Error('Connection refused'))
        }
        return Promise.resolve({ status: 200, data: { status: 'healthy' } })
      })

      const handler = findHandler('/services', 'get')
      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.query).to.equal('unhealthy')
      expect(jsonArg.services.connector).to.equal('healthy')
      expect(res.status.calledWith(503)).to.be.true
    })

    it('should return unhealthy with 503 when connector service is down', async () => {
      sinon.stub(axiosModule, 'get').callsFake((url: string) => {
        if (url.includes('8088')) {
          return Promise.reject(new Error('Connection refused'))
        }
        return Promise.resolve({ status: 200, data: { status: 'healthy' } })
      })

      const handler = findHandler('/services', 'get')
      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.connector).to.equal('unhealthy')
      expect(res.status.calledWith(503)).to.be.true
    })

    it('should return unhealthy when both services are down', async () => {
      sinon.stub(axiosModule, 'get').rejects(new Error('Connection refused'))

      const handler = findHandler('/services', 'get')
      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.query).to.equal('unhealthy')
      expect(jsonArg.services.connector).to.equal('unhealthy')
    })

    it('should return unhealthy when service returns non-healthy data', async () => {
      sinon.stub(axiosModule, 'get').resolves({
        status: 200,
        data: { status: 'degraded' },
      })

      const handler = findHandler('/services', 'get')
      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
    })

    it('should handle unexpected error in overall try-catch', async () => {
      // Make Promise.allSettled itself throw by breaking axiosModule
      sinon.stub(axiosModule, 'get').throws(new Error('Unexpected'))

      const handler = findHandler('/services', 'get')
      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      // Should hit the outer catch block
      expect(res.status.calledWith(503)).to.be.true
      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.query).to.equal('unknown')
      expect(jsonArg.services.connector).to.equal('unknown')
    })
  })
})
