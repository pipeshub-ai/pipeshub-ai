import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { CrawlingManagerContainer } from '../../../../src/modules/crawling_manager/container/cm_container'

describe('CrawlingManagerContainer', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('should be importable', () => {
    expect(CrawlingManagerContainer).to.be.a('function')
  })

  describe('static methods', () => {
    it('should have initialize static method', () => {
      expect(CrawlingManagerContainer.initialize).to.be.a('function')
    })

    it('should have getInstance static method', () => {
      expect(CrawlingManagerContainer.getInstance).to.be.a('function')
    })

    it('should have dispose static method', () => {
      expect(CrawlingManagerContainer.dispose).to.be.a('function')
    })
  })

  describe('getInstance', () => {
    it('should throw when container is not initialized', () => {
      const originalInstance = (CrawlingManagerContainer as any).instance
      ;(CrawlingManagerContainer as any).instance = null

      try {
        expect(() => CrawlingManagerContainer.getInstance()).to.throw(
          'Crawling Manager container not initialized',
        )
      } finally {
        ;(CrawlingManagerContainer as any).instance = originalInstance
      }
    })

    it('should return the container when initialized', () => {
      const mockContainer = { isBound: sinon.stub() }
      const originalInstance = (CrawlingManagerContainer as any).instance
      ;(CrawlingManagerContainer as any).instance = mockContainer

      try {
        const result = CrawlingManagerContainer.getInstance()
        expect(result).to.equal(mockContainer)
      } finally {
        ;(CrawlingManagerContainer as any).instance = originalInstance
      }
    })
  })

  describe('dispose', () => {
    it('should not throw when instance is null', async () => {
      const originalInstance = (CrawlingManagerContainer as any).instance
      ;(CrawlingManagerContainer as any).instance = null

      try {
        await CrawlingManagerContainer.dispose()
      } finally {
        ;(CrawlingManagerContainer as any).instance = originalInstance
      }
    })

    it('should set instance to null after dispose', async () => {
      const mockRedis = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }
      const mockCrawlingWorker = { close: sinon.stub().resolves() }
      const mockKvStore = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }
      const mockSyncEvents = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: any) => {
          const knownKeys = ['RedisService', 'SyncEventProducer']
          // Handle class-based bindings
          if (typeof key === 'function') {
            return true
          }
          return knownKeys.includes(key)
        }),
        get: sinon.stub().callsFake((key: any) => {
          if (key === 'RedisService') return mockRedis
          if (key === 'SyncEventProducer') return mockSyncEvents
          // Handle class-based keys (CrawlingWorkerService, KeyValueStoreService)
          if (typeof key === 'function') {
            if (key.name === 'CrawlingWorkerService') return mockCrawlingWorker
            if (key.name === 'KeyValueStoreService') return mockKvStore
          }
          return null
        }),
      }

      const originalInstance = (CrawlingManagerContainer as any).instance
      ;(CrawlingManagerContainer as any).instance = mockContainer

      try {
        await CrawlingManagerContainer.dispose()
        expect((CrawlingManagerContainer as any).instance).to.be.null
      } finally {
        if ((CrawlingManagerContainer as any).instance !== null) {
          ;(CrawlingManagerContainer as any).instance = originalInstance
        }
      }
    })

    it('should disconnect Redis when connected', async () => {
      const mockRedis = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: any) => key === 'RedisService'),
        get: sinon.stub().callsFake((key: any) => {
          if (key === 'RedisService') return mockRedis
          return null
        }),
      }

      const originalInstance = (CrawlingManagerContainer as any).instance
      ;(CrawlingManagerContainer as any).instance = mockContainer

      try {
        await CrawlingManagerContainer.dispose()
        expect(mockRedis.disconnect.calledOnce).to.be.true
      } finally {
        if ((CrawlingManagerContainer as any).instance !== null) {
          ;(CrawlingManagerContainer as any).instance = originalInstance
        }
      }
    })

    it('should not disconnect Redis when not connected', async () => {
      const mockRedis = { isConnected: sinon.stub().returns(false), disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: any) => key === 'RedisService'),
        get: sinon.stub().callsFake((key: any) => {
          if (key === 'RedisService') return mockRedis
          return null
        }),
      }

      const originalInstance = (CrawlingManagerContainer as any).instance
      ;(CrawlingManagerContainer as any).instance = mockContainer

      try {
        await CrawlingManagerContainer.dispose()
        expect(mockRedis.disconnect.called).to.be.false
      } finally {
        if ((CrawlingManagerContainer as any).instance !== null) {
          ;(CrawlingManagerContainer as any).instance = originalInstance
        }
      }
    })

    it('should handle errors during disconnect gracefully', async () => {
      const mockRedis = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().rejects(new Error('Disconnect failed')),
      }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: any) => key === 'RedisService'),
        get: sinon.stub().returns(mockRedis),
      }

      const originalInstance = (CrawlingManagerContainer as any).instance
      ;(CrawlingManagerContainer as any).instance = mockContainer

      try {
        await CrawlingManagerContainer.dispose()
        expect((CrawlingManagerContainer as any).instance).to.be.null
      } finally {
        if ((CrawlingManagerContainer as any).instance !== null) {
          ;(CrawlingManagerContainer as any).instance = originalInstance
        }
      }
    })
  })

  describe('initialize', () => {
    it('should accept configurationManagerConfig and appConfig parameters', () => {
      expect(CrawlingManagerContainer.initialize.length).to.equal(2)
    })
  })
})
