import 'reflect-metadata'
import { expect } from 'chai'
import path from 'path'
import { McpToolScopes } from '../../src/modules/oauth_provider/config/scopes.config'

/**
 * MCP clients ask for the scopes /mcp discovery lists (McpToolScopes), and
 * @pipeshub-ai/mcp offers a tool only when the token holds every scope it
 * requires. A scope a tool requires that discovery leaves out takes that whole
 * tool away from every new connection, so discovery must list each one.
 */
const nm = (p: string): string => path.join(__dirname, '../../node_modules', p)
// The mocha setup replaces the CJS view of this ESM package with a mock, so
// load the real files with a native dynamic import.
// eslint-disable-next-line @typescript-eslint/no-implied-eval -- ts-node compiles import() to the mocked require()
const nativeImport = new Function('p', 'return import(p)') as (p: string) => Promise<any>

describe('MCP tools and the scopes discovery lists', function () {
  this.timeout(120000)

  it('discovery lists every scope a /mcp tool requires', async () => {
    const { createMCPServer } = await nativeImport(nm('@pipeshub-ai/mcp/esm/mcp-server/server.js'))
    const { requiredOAuthScopes } = await nativeImport(nm('@pipeshub-ai/mcp/esm/mcp-server/oauth-scopes.js'))
    const { Client } = await nativeImport(nm('@modelcontextprotocol/sdk/dist/esm/client/index.js'))
    const { InMemoryTransport } = await nativeImport(nm('@modelcontextprotocol/sdk/dist/esm/inMemory.js'))
    const noop = (): void => {}
    const { server } = createMCPServer({
      logger: { level: 'error', info: noop, debug: noop, warning: noop, error: noop },
      dynamic: false,
      apps: true,
      serverURL: 'http://127.0.0.1:9/api/v1',
      getSDK: () => ({}),
    })
    const [a, b] = InMemoryTransport.createLinkedPair()
    await server.connect(a)
    const client = new Client({ name: 'contract', version: '1' })
    await client.connect(b)
    const names = (await client.listTools()).tools.map((t: { name: string }) => t.name)
    expect(names).to.include('pipeshub_directory')

    const advertised = new Set<string>(McpToolScopes)
    const missing = names.flatMap((name: string) =>
      [...((requiredOAuthScopes(name) as string[] | undefined) ?? [])]
        .filter((scope) => !advertised.has(scope))
        .map((scope) => `${name} requires ${scope}`),
    )
    expect(missing).to.deep.equal([])
  })
})
