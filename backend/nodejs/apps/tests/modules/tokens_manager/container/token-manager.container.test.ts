import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { TokenManagerContainer } from '../../../../src/modules/tokens_manager/container/token-manager.container'

describe('tokens_manager/container/token-manager.container', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('static methods', () => {
    it('should have initialize static method', () => {
      expect(TokenManagerContainer.initialize).to.be.a('function')
    })

    it('should have getInstance static method', () => {
      expect(TokenManagerContainer.getInstance).to.be.a('function')
    })

    it('should have dispose static method', () => {
      expect(TokenManagerContainer.dispose).to.be.a('function')
    })

    it('should accept configurationManagerConfig parameter', () => {
      expect(TokenManagerContainer.initialize.length).to.equal(1)
    })
  })

  describe('getInstance', () => {
    it('should throw when container is not initialized', () => {
      ;(TokenManagerContainer as any).instance = null
      expect(() => TokenManagerContainer.getInstance()).to.throw('Service container not initialized')
    })

    it('should return the container when initialized', () => {
      const mockContainer = { isBound: sinon.stub() }
      const originalInstance = (TokenManagerContainer as any).instance
      ;(TokenManagerContainer as any).instance = mockContainer

      try {
        const result = TokenManagerContainer.getInstance()
        expect(result).to.equal(mockContainer)
      } finally {
        ;(TokenManagerContainer as any).instance = originalInstance
      }
    })
  })

  describe('dispose', () => {
    it('should not throw when instance is null', async () => {
      ;(TokenManagerContainer as any).instance = null
      await TokenManagerContainer.dispose()
    })

    it('should disconnect all services on dispose', async () => {
      const mockMongo = { isConnected: sinon.stub().returns(true), destroy: sinon.stub().resolves() }
      const mockRedis = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }
      const mockMessageProducer = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) =>
          ['MongoService', 'RedisService', 'MessageProducer'].includes(key),
        ),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'MongoService') return mockMongo
          if (key === 'RedisService') return mockRedis
          if (key === 'MessageProducer') return mockMessageProducer
          return null
        }),
      };
      (TokenManagerContainer as any).instance = mockContainer

      await TokenManagerContainer.dispose()

      expect(mockRedis.disconnect.calledOnce).to.be.true
      expect(mockMessageProducer.disconnect.calledOnce).to.be.true
      expect(mockMongo.destroy.calledOnce).to.be.true
      expect((TokenManagerContainer as any).instance).to.be.null
    })

    it('should handle services that are not connected', async () => {
      const mockMongo = { isConnected: sinon.stub().returns(false), destroy: sinon.stub() }
      const mockRedis = { isConnected: sinon.stub().returns(false), disconnect: sinon.stub() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => ['MongoService', 'RedisService'].includes(key)),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'MongoService') return mockMongo
          if (key === 'RedisService') return mockRedis
          return null
        }),
      };
      (TokenManagerContainer as any).instance = mockContainer

      await TokenManagerContainer.dispose()

      expect(mockRedis.disconnect.called).to.be.false
      expect(mockMongo.destroy.called).to.be.false
    })

    it('should handle errors during disconnect gracefully', async () => {
      const mockRedis = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().rejects(new Error('Disconnect failed')),
      }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'RedisService'),
        get: sinon.stub().returns(mockRedis),
      };
      (TokenManagerContainer as any).instance = mockContainer

      await TokenManagerContainer.dispose()

      expect((TokenManagerContainer as any).instance).to.be.null
    })

    it('should handle missing service bindings gracefully', async () => {
      const mockContainer = {
        isBound: sinon.stub().returns(false),
        get: sinon.stub(),
      };
      (TokenManagerContainer as any).instance = mockContainer

      await TokenManagerContainer.dispose()

      expect((TokenManagerContainer as any).instance).to.be.null
      expect(mockContainer.get.called).to.be.false
    })

    it('should set instance to null even when errors occur', async () => {
      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'MongoService'),
        get: sinon.stub().callsFake(() => {
          throw new Error('Get failed')
        }),
      };
      (TokenManagerContainer as any).instance = mockContainer

      await TokenManagerContainer.dispose()

      expect((TokenManagerContainer as any).instance).to.be.null
    })

    it('should disconnect MessageProducer only', async () => {
      const mockMessageProducer = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) =>
          key === 'MessageProducer',
        ),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'MessageProducer') return mockMessageProducer
          return null
        }),
      };
      (TokenManagerContainer as any).instance = mockContainer

      await TokenManagerContainer.dispose()

      expect(mockMessageProducer.disconnect.calledOnce).to.be.true
    })
  })
})
