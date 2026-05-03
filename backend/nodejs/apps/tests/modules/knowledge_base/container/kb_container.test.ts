import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { KnowledgeBaseContainer } from '../../../../src/modules/knowledge_base/container/kb_container'

describe('KnowledgeBaseContainer', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('should be importable', () => {
    expect(KnowledgeBaseContainer).to.be.a('function')
  })

  describe('static methods', () => {
    it('should have initialize static method', () => {
      expect(KnowledgeBaseContainer.initialize).to.be.a('function')
    })

    it('should have getInstance static method', () => {
      expect(KnowledgeBaseContainer.getInstance).to.be.a('function')
    })

    it('should have dispose static method', () => {
      expect(KnowledgeBaseContainer.dispose).to.be.a('function')
    })
  })

  describe('getInstance', () => {
    it('should throw when container is not initialized', () => {
      const originalInstance = (KnowledgeBaseContainer as any).instance
      ;(KnowledgeBaseContainer as any).instance = null

      try {
        expect(() => KnowledgeBaseContainer.getInstance()).to.throw(
          'Service container not initialized',
        )
      } finally {
        ;(KnowledgeBaseContainer as any).instance = originalInstance
      }
    })

    it('should return the container when initialized', () => {
      const mockContainer = { isBound: sinon.stub() }
      const originalInstance = (KnowledgeBaseContainer as any).instance
      ;(KnowledgeBaseContainer as any).instance = mockContainer

      try {
        const result = KnowledgeBaseContainer.getInstance()
        expect(result).to.equal(mockContainer)
      } finally {
        ;(KnowledgeBaseContainer as any).instance = originalInstance
      }
    })
  })

  describe('initialize', () => {
    it('should accept configurationManagerConfig and appConfig parameters', () => {
      expect(KnowledgeBaseContainer.initialize.length).to.equal(2)
    })
  })

  describe('dispose', () => {
    it('should not throw when instance is null', async () => {
      const originalInstance = (KnowledgeBaseContainer as any).instance
      ;(KnowledgeBaseContainer as any).instance = null

      try {
        await KnowledgeBaseContainer.dispose()
      } finally {
        ;(KnowledgeBaseContainer as any).instance = originalInstance
      }
    })

    it('should disconnect MessageProducer and KV store', async () => {
      const mockMessageProducer = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }
      const mockKvStore = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) =>
          ['MessageProducer', 'KeyValueStoreService'].includes(key),
        ),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'MessageProducer') return mockMessageProducer
          if (key === 'KeyValueStoreService') return mockKvStore
          return null
        }),
      }

      const originalInstance = (KnowledgeBaseContainer as any).instance
      ;(KnowledgeBaseContainer as any).instance = mockContainer

      try {
        await KnowledgeBaseContainer.dispose()
        expect((KnowledgeBaseContainer as any).instance).to.be.null
        expect(mockMessageProducer.disconnect.calledOnce).to.be.true
        expect(mockKvStore.disconnect.calledOnce).to.be.true
      } finally {
        if ((KnowledgeBaseContainer as any).instance !== null) {
          ;(KnowledgeBaseContainer as any).instance = originalInstance
        }
      }
    })

    it('should not disconnect KV store when not connected', async () => {
      const mockKvStore = { isConnected: sinon.stub().returns(false), disconnect: sinon.stub() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'KeyValueStoreService'),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'KeyValueStoreService') return mockKvStore
          return null
        }),
      }

      const originalInstance = (KnowledgeBaseContainer as any).instance
      ;(KnowledgeBaseContainer as any).instance = mockContainer

      try {
        await KnowledgeBaseContainer.dispose()
        expect(mockKvStore.disconnect.called).to.be.false
      } finally {
        if ((KnowledgeBaseContainer as any).instance !== null) {
          ;(KnowledgeBaseContainer as any).instance = originalInstance
        }
      }
    })

    it('should handle missing service bindings gracefully', async () => {
      const mockContainer = {
        isBound: sinon.stub().returns(false),
        get: sinon.stub(),
      }

      const originalInstance = (KnowledgeBaseContainer as any).instance
      ;(KnowledgeBaseContainer as any).instance = mockContainer

      try {
        await KnowledgeBaseContainer.dispose()
        expect((KnowledgeBaseContainer as any).instance).to.be.null
      } finally {
        if ((KnowledgeBaseContainer as any).instance !== null) {
          ;(KnowledgeBaseContainer as any).instance = originalInstance
        }
      }
    })

    it('should handle errors during dispose gracefully', async () => {
      const mockMessageProducer = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().rejects(new Error('Disconnect failed')) }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'MessageProducer'),
        get: sinon.stub().returns(mockMessageProducer),
      }

      const originalInstance = (KnowledgeBaseContainer as any).instance
      ;(KnowledgeBaseContainer as any).instance = mockContainer

      try {
        await KnowledgeBaseContainer.dispose()
        // Should not throw
      } finally {
        if ((KnowledgeBaseContainer as any).instance !== null) {
          ;(KnowledgeBaseContainer as any).instance = originalInstance
        }
      }
    })
  })
})
