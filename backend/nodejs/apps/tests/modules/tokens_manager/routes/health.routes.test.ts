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

    it('should mark graphDb as unhealthy when arango health check fails', async () => {
      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').callsFake((url: string) => {
        if (url.includes('8529')) {
          return Promise.reject(new Error('ArangoDB connection refused'))
        }
        return Promise.resolve({ status: 200 })
      })

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.graphDb).to.equal('unhealthy')
    })

    it('should mark graphDb as unhealthy when neo4j health check fails', async () => {
      mockAppConfig.deployment.dataStoreType = 'neo4j'
      router = createHealthRouter(container, cmContainer)

      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').callsFake((url: string) => {
        if (url.includes('7474')) {
          return Promise.reject(new Error('Neo4j connection refused'))
        }
        return Promise.resolve({ status: 200 })
      })

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.graphDb).to.equal('unhealthy')
    })

    it('should convert neo4j bolt:// URI to http:// with port 7474', async () => {
      mockAppConfig.deployment.dataStoreType = 'neo4j'
      process.env.NEO4J_URI = 'bolt://neo4j-host:7687'
      router = createHealthRouter(container, cmContainer)

      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      const axiosStub = sinon.stub(axiosModule, 'get').resolves({ status: 200 })

      await handler({}, res, next)

      const neo4jCall = axiosStub.getCalls().find(
        (c: any) => c.args[0].includes('neo4j-host'),
      )
      expect(neo4jCall).to.exist
      expect(neo4jCall!.args[0]).to.equal('http://neo4j-host:7474')

      delete process.env.NEO4J_URI
    })

    it('should convert neo4j neo4j:// URI to http:// with port 7474', async () => {
      mockAppConfig.deployment.dataStoreType = 'neo4j'
      process.env.NEO4J_URI = 'neo4j://neo4j-host:7687'
      router = createHealthRouter(container, cmContainer)

      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      const axiosStub = sinon.stub(axiosModule, 'get').resolves({ status: 200 })

      await handler({}, res, next)

      const neo4jCall = axiosStub.getCalls().find(
        (c: any) => c.args[0].includes('neo4j-host'),
      )
      expect(neo4jCall).to.exist
      expect(neo4jCall!.args[0]).to.equal('http://neo4j-host:7474')

      delete process.env.NEO4J_URI
    })

    it('should mark vectorDb as unhealthy when qdrant health check fails', async () => {
      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').callsFake((url: string) => {
        if (url.includes('healthz')) {
          return Promise.reject(new Error('Qdrant connection refused'))
        }
        return Promise.resolve({ status: 200 })
      })

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.vectorDb).to.equal('unhealthy')
    })

    it('should mark vectorDb as unhealthy when qdrant returns non-200', async () => {
      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').callsFake((url: string) => {
        if (url.includes('healthz')) {
          return Promise.resolve({ status: 503 })
        }
        return Promise.resolve({ status: 200 })
      })

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.vectorDb).to.equal('unhealthy')
    })

    it('should mark graphDb as unhealthy when arango returns non-200', async () => {
      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').callsFake((url: string) => {
        if (url.includes('8529')) {
          return Promise.resolve({ status: 503 })
        }
        return Promise.resolve({ status: 200 })
      })

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.graphDb).to.equal('unhealthy')
    })

    it('should always return HTTP 200 even when services are unhealthy', async () => {
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

      expect(res.status.calledWith(200)).to.be.true
      expect(res.status.neverCalledWith(503)).to.be.true
    })

    it('should return valid ISO timestamp', async () => {
      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').resolves({ status: 200 })

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      const parsed = new Date(jsonArg.timestamp)
      expect(parsed.toISOString()).to.equal(jsonArg.timestamp)
    })

    it('should include all expected top-level keys in response', async () => {
      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').resolves({ status: 200 })

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg).to.have.property('status')
      expect(jsonArg).to.have.property('timestamp')
      expect(jsonArg).to.have.property('services')
      expect(jsonArg).to.have.property('serviceNames')
      expect(jsonArg).to.have.property('deployment')
    })

    it('should include all expected deployment keys', async () => {
      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').resolves({ status: 200 })

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.deployment).to.have.property('kvStoreType')
      expect(jsonArg.deployment).to.have.property('messageBrokerType')
      expect(jsonArg.deployment).to.have.property('graphDbType')
    })

    it('should call next() on unexpected top-level exception', async () => {
      const handler = findHandler('/', 'get')
      const res = mockRes()
      res.json = sinon.stub().throws(new Error('unexpected serialization error'))
      const next = sinon.stub()
      await handler({}, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should report healthy overall when only vectorDb is unhealthy', async () => {
      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      sinon.stub(axiosModule, 'get').callsFake((url: string) => {
        if (url.includes('healthz')) {
          return Promise.reject(new Error('Qdrant down'))
        }
        return Promise.resolve({ status: 200 })
      })

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.vectorDb).to.equal('unhealthy')
      expect(jsonArg.services.redis).to.equal('healthy')
      expect(jsonArg.services.messageBroker).to.equal('healthy')
      expect(jsonArg.services.mongodb).to.equal('healthy')
    })

    it('should check qdrant at the correct URL from config', async () => {
      mockAppConfig.qdrant = { host: 'qdrant.example.com', port: 6333, apiKey: '', grpcPort: 6334 }
      router = createHealthRouter(container, cmContainer)

      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      const axiosStub = sinon.stub(axiosModule, 'get').resolves({ status: 200 })

      await handler({}, res, next)

      const qdrantCall = axiosStub.getCalls().find(
        (c: any) => c.args[0].includes('qdrant.example.com'),
      )
      expect(qdrantCall).to.exist
      expect(qdrantCall!.args[0]).to.equal('http://qdrant.example.com:6333/healthz')
    })

    it('should check arango at the correct URL from config', async () => {
      mockAppConfig.arango = { url: 'http://arango.example.com:8529', db: 'test', username: 'root', password: '' }
      router = createHealthRouter(container, cmContainer)

      const handler = findHandler('/', 'get')
      const res = mockRes()
      const next = sinon.stub()

      const axiosModule = require('axios')
      const axiosStub = sinon.stub(axiosModule, 'get').resolves({ status: 200 })

      await handler({}, res, next)

      const arangoCall = axiosStub.getCalls().find(
        (c: any) => c.args[0].includes('arango.example.com'),
      )
      expect(arangoCall).to.exist
      expect(arangoCall!.args[0]).to.equal('http://arango.example.com:8529/_api/version')
    })
  })

  // =========================================================================
  // Deployment configuration combinations
  // =========================================================================
  describe('GET / - deployment configuration combinations', () => {
    const deploymentCombinations = [
      {
        name: 'arangodb + kafka + etcd',
        config: { dataStoreType: 'arangodb', messageBrokerType: 'kafka', kvStoreType: 'etcd', vectorDbType: 'qdrant' },
        expectedGraphDbName: 'ArangoDB',
        expectedBrokerName: 'Kafka',
        hasKVStoreService: true,
      },
      {
        name: 'neo4j + kafka + etcd',
        config: { dataStoreType: 'neo4j', messageBrokerType: 'kafka', kvStoreType: 'etcd', vectorDbType: 'qdrant' },
        expectedGraphDbName: 'Neo4j',
        expectedBrokerName: 'Kafka',
        hasKVStoreService: true,
      },
      {
        name: 'arangodb + redis + redis',
        config: { dataStoreType: 'arangodb', messageBrokerType: 'redis', kvStoreType: 'redis', vectorDbType: 'qdrant' },
        expectedGraphDbName: 'ArangoDB',
        expectedBrokerName: 'Redis Streams',
        hasKVStoreService: false,
      },
      {
        name: 'neo4j + redis + redis',
        config: { dataStoreType: 'neo4j', messageBrokerType: 'redis', kvStoreType: 'redis', vectorDbType: 'qdrant' },
        expectedGraphDbName: 'Neo4j',
        expectedBrokerName: 'Redis Streams',
        hasKVStoreService: false,
      },
      {
        name: 'arangodb + kafka + redis',
        config: { dataStoreType: 'arangodb', messageBrokerType: 'kafka', kvStoreType: 'redis', vectorDbType: 'qdrant' },
        expectedGraphDbName: 'ArangoDB',
        expectedBrokerName: 'Kafka',
        hasKVStoreService: false,
      },
      {
        name: 'neo4j + redis + etcd',
        config: { dataStoreType: 'neo4j', messageBrokerType: 'redis', kvStoreType: 'etcd', vectorDbType: 'qdrant' },
        expectedGraphDbName: 'Neo4j',
        expectedBrokerName: 'Redis Streams',
        hasKVStoreService: true,
      },
    ]

    for (const combo of deploymentCombinations) {
      describe(`deployment: ${combo.name}`, () => {
        beforeEach(() => {
          mockAppConfig.deployment = { ...combo.config }
          router = createHealthRouter(container, cmContainer)
        })

        it(`should show graphDb as ${combo.expectedGraphDbName}`, async () => {
          const handler = findHandler('/', 'get')
          const res = mockRes()
          const next = sinon.stub()

          const axiosModule = require('axios')
          sinon.stub(axiosModule, 'get').resolves({ status: 200 })

          await handler({}, res, next)

          const jsonArg = res.json.firstCall.args[0]
          expect(jsonArg.serviceNames.graphDb).to.equal(combo.expectedGraphDbName)
          expect(jsonArg.deployment.graphDbType).to.equal(combo.config.dataStoreType)
        })

        it(`should show messageBroker as ${combo.expectedBrokerName}`, async () => {
          const handler = findHandler('/', 'get')
          const res = mockRes()
          const next = sinon.stub()

          const axiosModule = require('axios')
          sinon.stub(axiosModule, 'get').resolves({ status: 200 })

          await handler({}, res, next)

          const jsonArg = res.json.firstCall.args[0]
          expect(jsonArg.serviceNames.messageBroker).to.equal(combo.expectedBrokerName)
          expect(jsonArg.deployment.messageBrokerType).to.equal(combo.config.messageBrokerType)
        })

        it(`should ${combo.hasKVStoreService ? 'include' : 'exclude'} KVStoreservice`, async () => {
          const handler = findHandler('/', 'get')
          const res = mockRes()
          const next = sinon.stub()

          const axiosModule = require('axios')
          sinon.stub(axiosModule, 'get').resolves({ status: 200 })

          await handler({}, res, next)

          const jsonArg = res.json.firstCall.args[0]
          if (combo.hasKVStoreService) {
            expect(jsonArg.services.KVStoreservice).to.exist
            expect(jsonArg.serviceNames.KVStoreservice).to.equal('etcd')
          } else {
            expect(jsonArg.services.KVStoreservice).to.be.undefined
          }
        })

        it('should report correct kvStoreType in deployment', async () => {
          const handler = findHandler('/', 'get')
          const res = mockRes()
          const next = sinon.stub()

          const axiosModule = require('axios')
          sinon.stub(axiosModule, 'get').resolves({ status: 200 })

          await handler({}, res, next)

          const jsonArg = res.json.firstCall.args[0]
          expect(jsonArg.deployment.kvStoreType).to.equal(combo.config.kvStoreType)
        })
      })
    }
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

    it('should still be healthy when only indexing is down (non-critical)', async () => {
      sinon.stub(axiosModule, 'get').callsFake((url: string) => {
        if (url.includes('8091')) {
          return Promise.reject(new Error('Indexing down'))
        }
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
      expect(jsonArg.services.indexing).to.equal('unhealthy')
    })

    it('should still be healthy when only docling is down (non-critical)', async () => {
      sinon.stub(axiosModule, 'get').callsFake((url: string) => {
        if (url.includes('8081')) {
          return Promise.reject(new Error('Docling down'))
        }
        return Promise.resolve({ status: 200, data: { status: 'healthy' } })
      })

      const handler = findHandler('/services', 'get')
      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('healthy')
      expect(jsonArg.services.docling).to.equal('unhealthy')
    })

    it('should still be healthy when both indexing and docling are down', async () => {
      sinon.stub(axiosModule, 'get').callsFake((url: string) => {
        if (url.includes('8091') || url.includes('8081')) {
          return Promise.reject(new Error('Service down'))
        }
        return Promise.resolve({ status: 200, data: { status: 'healthy' } })
      })

      const handler = findHandler('/services', 'get')
      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('healthy')
      expect(jsonArg.services.indexing).to.equal('unhealthy')
      expect(jsonArg.services.docling).to.equal('unhealthy')
      expect(jsonArg.services.query).to.equal('healthy')
      expect(jsonArg.services.connector).to.equal('healthy')
    })

    it('should use DOCLING_BACKEND env var for docling health check URL', async () => {
      process.env.DOCLING_BACKEND = 'http://custom-docling:9090'

      // Re-create router to pick up new env var
      router = createHealthRouter(container, cmContainer)
      const handler = router.stack.find(
        (l: any) => l.route && l.route.path === '/services' && l.route.methods.get,
      )?.route.stack[0].handle

      const axiosStub = sinon.stub(axiosModule, 'get').resolves({
        status: 200,
        data: { status: 'healthy' },
      })

      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      const doclingCall = axiosStub.getCalls().find(
        (c: any) => c.args[0].includes('custom-docling:9090'),
      )
      expect(doclingCall).to.exist
      expect(doclingCall!.args[0]).to.equal('http://custom-docling:9090/health')

      delete process.env.DOCLING_BACKEND
    })

    it('should default docling URL to http://localhost:8081 when env not set', async () => {
      delete process.env.DOCLING_BACKEND
      router = createHealthRouter(container, cmContainer)
      const handler = router.stack.find(
        (l: any) => l.route && l.route.path === '/services' && l.route.methods.get,
      )?.route.stack[0].handle

      const axiosStub = sinon.stub(axiosModule, 'get').resolves({
        status: 200,
        data: { status: 'healthy' },
      })

      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      const doclingCall = axiosStub.getCalls().find(
        (c: any) => c.args[0].includes('8081'),
      )
      expect(doclingCall).to.exist
      expect(doclingCall!.args[0]).to.equal('http://localhost:8081/health')
    })

    it('should mark service as unhealthy when it returns 200 but no data', async () => {
      sinon.stub(axiosModule, 'get').resolves({ status: 200, data: null })

      const handler = findHandler('/services', 'get')
      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.query).to.equal('unhealthy')
      expect(jsonArg.services.connector).to.equal('unhealthy')
    })

    it('should mark service as unhealthy when it returns non-200 status', async () => {
      sinon.stub(axiosModule, 'get').resolves({
        status: 503,
        data: { status: 'healthy' },
      })

      const handler = findHandler('/services', 'get')
      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.query).to.equal('unhealthy')
    })

    it('should always return HTTP 200 for /services even when all are down', async () => {
      sinon.stub(axiosModule, 'get').rejects(new Error('All services down'))

      const handler = findHandler('/services', 'get')
      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.status.neverCalledWith(503)).to.be.true
    })

    it('should include valid timestamp in /services response', async () => {
      sinon.stub(axiosModule, 'get').resolves({
        status: 200,
        data: { status: 'healthy' },
      })

      const handler = findHandler('/services', 'get')
      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.timestamp).to.be.a('string')
      const parsed = new Date(jsonArg.timestamp)
      expect(parsed.toISOString()).to.equal(jsonArg.timestamp)
    })

    it('should include all four service keys in response', async () => {
      sinon.stub(axiosModule, 'get').resolves({
        status: 200,
        data: { status: 'healthy' },
      })

      const handler = findHandler('/services', 'get')
      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.services).to.have.property('query')
      expect(jsonArg.services).to.have.property('connector')
      expect(jsonArg.services).to.have.property('indexing')
      expect(jsonArg.services).to.have.property('docling')
    })

    it('should include all four service keys as unknown in error fallback', async () => {
      sinon.stub(axiosModule, 'get').throws(new Error('Unexpected'))

      const handler = findHandler('/services', 'get')
      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.services.query).to.equal('unknown')
      expect(jsonArg.services.connector).to.equal('unknown')
      expect(jsonArg.services.indexing).to.equal('unknown')
      expect(jsonArg.services.docling).to.equal('unknown')
    })

    it('should report per-service status independently', async () => {
      sinon.stub(axiosModule, 'get').callsFake((url: string) => {
        if (url.includes('8000')) {
          return Promise.resolve({ status: 200, data: { status: 'healthy' } })
        }
        if (url.includes('8088')) {
          return Promise.reject(new Error('Connector down'))
        }
        if (url.includes('8091')) {
          return Promise.resolve({ status: 200, data: { status: 'healthy' } })
        }
        // docling
        return Promise.reject(new Error('Docling down'))
      })

      const handler = findHandler('/services', 'get')
      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('unhealthy')
      expect(jsonArg.services.query).to.equal('healthy')
      expect(jsonArg.services.connector).to.equal('unhealthy')
      expect(jsonArg.services.indexing).to.equal('healthy')
      expect(jsonArg.services.docling).to.equal('unhealthy')
    })

    it('should use correct backend URLs from appConfig', async () => {
      const axiosStub = sinon.stub(axiosModule, 'get').resolves({
        status: 200,
        data: { status: 'healthy' },
      })

      const handler = findHandler('/services', 'get')
      const res = mockRes()
      const next = sinon.stub()

      await handler({}, res, next)

      const urls = axiosStub.getCalls().map((c: any) => c.args[0])
      expect(urls).to.include('http://localhost:8000/health')
      expect(urls).to.include('http://localhost:8088/health')
      expect(urls).to.include('http://localhost:8091/health')
    })
  })
})
