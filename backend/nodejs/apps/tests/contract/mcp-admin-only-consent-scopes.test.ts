import 'reflect-metadata'
import { expect } from 'chai'
import path from 'path'
import { AdminOnlyConsentScopes } from '../../src/modules/oauth_provider/config/scopes.config'

/**
 * A non-admin's /mcp token is issued without AdminOnlyConsentScopes. That is
 * safe only while no /mcp tool requires one of them: @pipeshub-ai/mcp offers a
 * tool only when the token holds every scope it requires, so such a scope would
 * take the whole tool away from members, not just its admin-only call.
 */
const nm = (p: string): string => path.join(__dirname, '../../node_modules', p)
// The mocha setup replaces the CJS view of this ESM package with a mock, so
// load the real files with a native dynamic import.
const nativeImport = new Function('p', 'return import(p)') as (p: string) => Promise<any>

describe('MCP tools and the scopes a non-admin is not asked for', function () {
  this.timeout(120000)

  it('no /mcp tool requires a scope a non-admin is not asked for', async () => {
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

    const needed = names.flatMap((name: string) =>
      [...((requiredOAuthScopes(name) as string[] | undefined) ?? [])]
        .filter((scope) => AdminOnlyConsentScopes.has(scope))
        .map((scope) => `${name} requires ${scope}`),
    )
    expect(needed).to.deep.equal([])
  })
})
