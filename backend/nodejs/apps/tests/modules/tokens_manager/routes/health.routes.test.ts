import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ConfigService } from '../../../../src/modules/tokens_manager/services/cm.service'
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
  let mockConfigService: any

  beforeEach(() => {
    mockConfigService = {
      readDeploymentConfig: sinon.stub().resolves({}),
    }
    sinon.stub(ConfigService, 'getInstance').returns(mockConfigService as any)

    mockRedis = { get: sinon.stub().resolves(null) }
    mockKafka = { healthCheck: sinon.stub().resolves(true) }
    mockMongo = { healthCheck: sinon.stub().resolves(true) }
    mockKV = { healthCheck: sinon.stub().resolves(true) }
    mockAppConfig = {
      aiBackend: 'http://localhost:8000',
      connectorBackend: 'http://localhost:8088',
      indexingBackend: 'http://localhost:8091',
      qdrant: { host: 'localhost', port: 6333 },
      arango: { url: 'http://localhost:8529' },
      deployment: {
        dataStoreType: 'arangodb',
        messageBrokerType: 'kafka',
        kvStoreType: 'etcd',
        vectorDbType: 'qdrant',
      },
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
    (ConfigService as any).instance = undefined
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

      // Stub axios for graph DB and vector DB health checks
      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').resolves({ status: 200 })

      await handler({}, res, next)

      expect(res.status.calledWith(200)).to.be.true
      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('healthy')
      expect(jsonArg.services.redis).to.equal('healthy')
      expect(jsonArg.services.messageBroker).to.equal('healthy')
      expect(jsonArg.services.mongodb).to.equal('healthy')
      expect(jsonArg.services.KVStoreservice).to.equal('healthy')
      expect(jsonArg.timestamp).to.be.a('string')
    })

    it('should include deployment info in response', async () => {
      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').resolves({ status: 200 })

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.deployment).to.exist
      expect(jsonArg.deployment.kvStoreType).to.equal('etcd')
      expect(jsonArg.deployment.messageBrokerType).to.equal('kafka')
      expect(jsonArg.deployment.graphDbType).to.equal('arangodb')
    })

    it('should include serviceNames in response', async () => {
      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').resolves({ status: 200 })

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.serviceNames).to.exist
      expect(jsonArg.serviceNames.redis).to.equal('Redis')
      expect(jsonArg.serviceNames.mongodb).to.equal('MongoDB')
      expect(jsonArg.serviceNames.messageBroker).to.equal('Kafka')
      expect(jsonArg.serviceNames.graphDb).to.equal('ArangoDB')
    })

    it('should show Neo4j in serviceNames when dataStoreType is neo4j', async () => {
      mockAppConfig.deployment.dataStoreType = 'neo4j'
      router = createHealthRouter(container, cmContainer)

      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').resolves({ status: 200 })

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.serviceNames.graphDb).to.equal('Neo4j')
      expect(jsonArg.deployment.graphDbType).to.equal('neo4j')
    })

    it('should show Redis Streams in serviceNames when messageBrokerType is redis', async () => {
      mockAppConfig.deployment.messageBrokerType = 'redis'
      router = createHealthRouter(container, cmContainer)

      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').resolves({ status: 200 })

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.serviceNames.messageBroker).to.equal('Redis Streams')
    })

    it('should not include KVStoreservice when kvStoreType is redis', async () => {
      mockAppConfig.deployment.kvStoreType = 'redis'
      router = createHealthRouter(container, cmContainer)

      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').resolves({ status: 200 })

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.services.KVStoreservice).to.be.undefined
    })

    it('should mark redis as unhealthy when redis throws', async () => {
      mockRedis.get.rejects(new Error('Redis connection failed'))
      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').resolves({ status: 200 })

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

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').resolves({ status: 200 })

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.messageBroker).to.equal('unhealthy')
    })

    it('should mark mongodb as unhealthy when mongo healthCheck returns false', async () => {
      mockMongo.healthCheck.resolves(false)
      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').resolves({ status: 200 })

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

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').resolves({ status: 200 })

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

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').resolves({ status: 200 })

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

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').resolves({ status: 200 })

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.KVStoreservice).to.equal('unhealthy')
    })

    it('should use fresh deployment config from ConfigService when available', async () => {
      mockConfigService.readDeploymentConfig.resolves({
        dataStoreType: 'neo4j',
        messageBrokerType: 'redis',
        kvStoreType: 'redis',
        vectorDbType: 'qdrant',
      })

      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').resolves({ status: 200 })

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.deployment.graphDbType).to.equal('neo4j')
      expect(jsonArg.deployment.messageBrokerType).to.equal('redis')
      expect(jsonArg.serviceNames.graphDb).to.equal('Neo4j')
      expect(jsonArg.serviceNames.messageBroker).to.equal('Redis Streams')
      expect(jsonArg.services.KVStoreservice).to.be.undefined
    })

    it('should fall back to default deployment config when readDeploymentConfig fails', async () => {
      mockConfigService.readDeploymentConfig.rejects(new Error('etcd unavailable'))

      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').resolves({ status: 200 })

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.deployment.kvStoreType).to.equal('etcd')
      expect(jsonArg.deployment.messageBrokerType).to.equal('kafka')
      expect(jsonArg.deployment.graphDbType).to.equal('arangodb')
    })

    it('should fall back to default when readDeploymentConfig returns empty object', async () => {
      mockConfigService.readDeploymentConfig.resolves({})

      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').resolves({ status: 200 })

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.deployment.kvStoreType).to.equal('etcd')
      expect(jsonArg.deployment.messageBrokerType).to.equal('kafka')
      expect(jsonArg.deployment.graphDbType).to.equal('arangodb')
    })

    it('should use default values for missing fields in fresh config', async () => {
      mockConfigService.readDeploymentConfig.resolves({
        dataStoreType: 'neo4j',
      })

      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').resolves({ status: 200 })

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.deployment.graphDbType).to.equal('neo4j')
      expect(jsonArg.deployment.messageBrokerType).to.equal('kafka')
      expect(jsonArg.deployment.kvStoreType).to.equal('etcd')
    })

    it('should mark all services as unhealthy when all fail', async () => {
      mockRedis.get.rejects(new Error('Redis down'))
      mockKafka.healthCheck.rejects(new Error('Kafka down'))
      mockMongo.healthCheck.rejects(new Error('Mongo down'))
      mockKV.healthCheck.rejects(new Error('KV down'))

      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').rejects(new Error('Connection refused'))

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.redis).to.equal('unhealthy')
      expect(jsonArg.services.messageBroker).to.equal('unhealthy')
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

    it('should return unhealthy when ai service is down', async () => {
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

      expect(res.status.calledWith(200)).to.be.true
      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.query).to.equal('unhealthy')
      expect(jsonArg.services.connector).to.equal('healthy')
    })

    it('should return unhealthy when connector service is down', async () => {
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

      expect(res.status.calledWith(200)).to.be.true
      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.connector).to.equal('unhealthy')
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

      expect(res.status.calledWith(200)).to.be.true
      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.query).to.equal('unknown')
      expect(jsonArg.services.connector).to.equal('unknown')
    })
  })
})
