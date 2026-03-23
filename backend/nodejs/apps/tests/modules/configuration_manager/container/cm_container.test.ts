import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ConfigurationManagerContainer } from '../../../../src/modules/configuration_manager/container/cm_container'

describe('ConfigurationManagerContainer', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('should be importable', () => {
    expect(ConfigurationManagerContainer).to.be.a('function')
  })

  describe('static methods', () => {
    it('should have initialize static method', () => {
      expect(ConfigurationManagerContainer.initialize).to.be.a('function')
    })

    it('should have getInstance static method', () => {
      expect(ConfigurationManagerContainer.getInstance).to.be.a('function')
    })

    it('should have dispose static method', () => {
      expect(ConfigurationManagerContainer.dispose).to.be.a('function')
    })
  })

  describe('getInstance', () => {
    it('should throw when container is not initialized', () => {
      const originalInstance = (ConfigurationManagerContainer as any).instance
      ;(ConfigurationManagerContainer as any).instance = null

      try {
        expect(() => ConfigurationManagerContainer.getInstance()).to.throw(
          'Service container not initialized',
        )
      } finally {
        ;(ConfigurationManagerContainer as any).instance = originalInstance
      }
    })

    it('should return the container when initialized', () => {
      const mockContainer = { isBound: sinon.stub() }
      const originalInstance = (ConfigurationManagerContainer as any).instance
      ;(ConfigurationManagerContainer as any).instance = mockContainer

      try {
        const result = ConfigurationManagerContainer.getInstance()
        expect(result).to.equal(mockContainer)
      } finally {
        ;(ConfigurationManagerContainer as any).instance = originalInstance
      }
    })
  })

  describe('initialize', () => {
    it('should accept configurationManagerConfig and appConfig parameters', () => {
      expect(ConfigurationManagerContainer.initialize.length).to.equal(2)
    })
  })

  describe('dispose', () => {
    it('should not throw when instance is null', async () => {
      const originalInstance = (ConfigurationManagerContainer as any).instance
      ;(ConfigurationManagerContainer as any).instance = null

      try {
        await ConfigurationManagerContainer.dispose()
      } finally {
        ;(ConfigurationManagerContainer as any).instance = originalInstance
      }
    })

    it('should disconnect all services and set instance to null', async () => {
      const mockKvStore = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }
      const mockEntityEvents = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }
      const mockSyncEvents = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) =>
          ['KeyValueStoreService', 'EntitiesEventProducer', 'SyncEventProducer'].includes(key),
        ),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'KeyValueStoreService') return mockKvStore
          if (key === 'EntitiesEventProducer') return mockEntityEvents
          if (key === 'SyncEventProducer') return mockSyncEvents
          return null
        }),
      }

      const originalInstance = (ConfigurationManagerContainer as any).instance
      ;(ConfigurationManagerContainer as any).instance = mockContainer

      try {
        await ConfigurationManagerContainer.dispose()
        expect((ConfigurationManagerContainer as any).instance).to.be.null
        expect(mockKvStore.disconnect.calledOnce).to.be.true
        expect(mockEntityEvents.disconnect.calledOnce).to.be.true
        expect(mockSyncEvents.disconnect.calledOnce).to.be.true
      } finally {
        if ((ConfigurationManagerContainer as any).instance !== null) {
          ;(ConfigurationManagerContainer as any).instance = originalInstance
        }
      }
    })

    it('should not disconnect services when they are not connected', async () => {
      const mockKvStore = { isConnected: sinon.stub().returns(false), disconnect: sinon.stub() }
      const mockEntityEvents = { isConnected: sinon.stub().returns(false), disconnect: sinon.stub() }
      const mockSyncEvents = { isConnected: sinon.stub().returns(false), disconnect: sinon.stub() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) =>
          ['KeyValueStoreService', 'EntitiesEventProducer', 'SyncEventProducer'].includes(key),
        ),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'KeyValueStoreService') return mockKvStore
          if (key === 'EntitiesEventProducer') return mockEntityEvents
          if (key === 'SyncEventProducer') return mockSyncEvents
          return null
        }),
      }

      const originalInstance = (ConfigurationManagerContainer as any).instance
      ;(ConfigurationManagerContainer as any).instance = mockContainer

      try {
        await ConfigurationManagerContainer.dispose()
        expect(mockKvStore.disconnect.called).to.be.false
        expect(mockEntityEvents.disconnect.called).to.be.false
        expect(mockSyncEvents.disconnect.called).to.be.false
      } finally {
        if ((ConfigurationManagerContainer as any).instance !== null) {
          ;(ConfigurationManagerContainer as any).instance = originalInstance
        }
      }
    })

    it('should handle missing service bindings gracefully', async () => {
      const mockContainer = {
        isBound: sinon.stub().returns(false),
        get: sinon.stub(),
      }

      const originalInstance = (ConfigurationManagerContainer as any).instance
      ;(ConfigurationManagerContainer as any).instance = mockContainer

      try {
        await ConfigurationManagerContainer.dispose()
        expect((ConfigurationManagerContainer as any).instance).to.be.null
      } finally {
        if ((ConfigurationManagerContainer as any).instance !== null) {
          ;(ConfigurationManagerContainer as any).instance = originalInstance
        }
      }
    })

    it('should handle errors during disconnect gracefully', async () => {
      const mockKvStore = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().rejects(new Error('KV store disconnect failed')),
      }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'KeyValueStoreService'),
        get: sinon.stub().returns(mockKvStore),
      }

      const originalInstance = (ConfigurationManagerContainer as any).instance
      ;(ConfigurationManagerContainer as any).instance = mockContainer

      try {
        await ConfigurationManagerContainer.dispose()
        expect((ConfigurationManagerContainer as any).instance).to.be.null
      } finally {
        if ((ConfigurationManagerContainer as any).instance !== null) {
          ;(ConfigurationManagerContainer as any).instance = originalInstance
        }
      }
    })
  })
})
