import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ToolsetsContainer } from '../../../../src/modules/toolsets/container/toolsets.container'

describe('toolsets/container/toolsets.container', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('getInstance', () => {
    it('should throw when container not initialized', () => {
      // Reset instance for clean test
      (ToolsetsContainer as any).instance = null
      expect(() => ToolsetsContainer.getInstance()).to.throw('Service container not initialized')
    })
  })

  describe('dispose', () => {
    it('should not throw when instance is null', async () => {
      (ToolsetsContainer as any).instance = null
      // Should complete without throwing
      let threw = false
      try {
        await ToolsetsContainer.dispose()
      } catch {
        threw = true
      }
      expect(threw).to.be.false
    })

    it('should disconnect services and set instance to null', async () => {
      const mockEntityEventsService = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().resolves(),
      }
      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'EntitiesEventProducer'),
        get: sinon.stub().returns(mockEntityEventsService),
      };
      (ToolsetsContainer as any).instance = mockContainer

      await ToolsetsContainer.dispose()

      expect(mockEntityEventsService.disconnect.calledOnce).to.be.true
      expect((ToolsetsContainer as any).instance).to.be.null
    })

    it('should handle errors during disconnect gracefully', async () => {
      const mockEntityEventsService = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().rejects(new Error('disconnect failed')),
      }
      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'EntitiesEventProducer'),
        get: sinon.stub().returns(mockEntityEventsService),
      };
      (ToolsetsContainer as any).instance = mockContainer

      await ToolsetsContainer.dispose()

      expect((ToolsetsContainer as any).instance).to.be.null
    })
  })
})
