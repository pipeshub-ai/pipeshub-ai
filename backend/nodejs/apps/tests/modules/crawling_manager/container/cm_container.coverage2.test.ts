import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { CrawlingManagerContainer, setupCrawlingDependencies } from '../../../../src/modules/crawling_manager/container/cm_container'
import { Container } from 'inversify'
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'

describe('CrawlingManagerContainer - coverage', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('setupCrawlingDependencies', () => {
    it('should bind RedisConfig and services to container', () => {
      const container = new Container()

      // Bind Logger first (required by services)
      container.bind<any>('Logger').toConstantValue({
        debug: sinon.stub(),
        info: sinon.stub(),
        error: sinon.stub(),
        warn: sinon.stub(),
      })
      container.bind<any>('AppConfig').toConstantValue({
        redis: { host: 'localhost', port: 6379 },
        kafka: { brokers: ['localhost:9092'] },
      })
      container.bind<any>('ConfigurationManagerConfig').toConstantValue({
        host: 'localhost',
        port: 2379,
      })

      const redisConfig = { host: 'localhost', port: 6379 }
      setupCrawlingDependencies(container, redisConfig)

      expect(container.isBound('RedisConfig')).to.be.true
    })
  })

  describe('getInstance', () => {
    it('should throw when not initialized', () => {
      // Reset the static instance
      ;(CrawlingManagerContainer as any).instance = null

      expect(() => CrawlingManagerContainer.getInstance()).to.throw(
        'Crawling Manager container not initialized',
      )
    })
  })

  describe('dispose', () => {
    it('should handle dispose when no instance', async () => {
      ;(CrawlingManagerContainer as any).instance = null
      // Should not throw
      await CrawlingManagerContainer.dispose()
    })

    it('should handle dispose with mock instance', async () => {
      const mockRedis = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().resolves(),
      }
      const mockKvStore = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().resolves(),
      }
      const mockSyncEvents = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().resolves(),
      }
      const mockCrawlingWorker = {
        close: sinon.stub().resolves(),
      }

      const container = new Container()
      container.bind<any>('RedisService').toConstantValue(mockRedis)
      container.bind<any>(KeyValueStoreService).toConstantValue(mockKvStore)
      container.bind<any>('SyncEventProducer').toConstantValue(mockSyncEvents)
      // We need the class symbol
      const { CrawlingWorkerService } = require('../../../../src/modules/crawling_manager/services/crawling_worker')
      container.bind<any>(CrawlingWorkerService).toConstantValue(mockCrawlingWorker)

      ;(CrawlingManagerContainer as any).instance = container

      await CrawlingManagerContainer.dispose()
      expect(mockRedis.disconnect.calledOnce).to.be.true
      expect(mockKvStore.disconnect.calledOnce).to.be.true
      expect(mockSyncEvents.disconnect.calledOnce).to.be.true
      expect(mockCrawlingWorker.close.calledOnce).to.be.true
    })

    it('should handle dispose when services are not connected', async () => {
      const mockRedis = {
        isConnected: sinon.stub().returns(false),
        disconnect: sinon.stub().resolves(),
      }

      const container = new Container()
      container.bind<any>('RedisService').toConstantValue(mockRedis)

      ;(CrawlingManagerContainer as any).instance = container

      await CrawlingManagerContainer.dispose()
      expect(mockRedis.disconnect.called).to.be.false
    })

    it('should handle dispose errors gracefully', async () => {
      const container = new Container()
      container.bind<any>('RedisService').toConstantValue({
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().rejects(new Error('disconnect failed')),
      })

      ;(CrawlingManagerContainer as any).instance = container

      // Should not throw
      await CrawlingManagerContainer.dispose()
      expect((CrawlingManagerContainer as any).instance).to.be.null
    })
  })
})
