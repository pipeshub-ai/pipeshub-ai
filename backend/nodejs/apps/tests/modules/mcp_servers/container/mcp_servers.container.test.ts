import 'reflect-metadata'
import { expect } from 'chai'
import { McpServersContainer } from '../../../../src/modules/mcp_servers/container/mcp_servers.container'

describe('mcp_servers/container/mcp_servers.container', () => {
  describe('getInstance', () => {
    it('should throw when container not initialized', () => {
      (McpServersContainer as any).instance = null
      expect(() => McpServersContainer.getInstance()).to.throw('MCP servers container not initialized')
    })

    it('should return the initialized container', () => {
      const fakeContainer: any = { get: () => undefined }
      ;(McpServersContainer as any).instance = fakeContainer
      expect(McpServersContainer.getInstance()).to.equal(fakeContainer)
      ;(McpServersContainer as any).instance = null
    })
  })
})
