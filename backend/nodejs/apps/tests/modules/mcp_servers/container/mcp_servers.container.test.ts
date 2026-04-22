import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { McpServersContainer } from '../../../../src/modules/mcp_servers/container/mcp_servers.container'

describe('mcp_servers/container/mcp_servers.container', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('getInstance', () => {
    it('should throw when getInstance called before initialize', () => {
      ;(McpServersContainer as any).instance = null
      expect(() => McpServersContainer.getInstance()).to.throw(/not initialized/)
    })
  })

  describe('dispose', () => {
    it('should handle dispose when not initialized', async () => {
      ;(McpServersContainer as any).instance = null
      let threw = false
      try {
        await McpServersContainer.dispose()
      } catch {
        threw = true
      }
      expect(threw).to.be.false
    })
  })
})
