import 'reflect-metadata'
import { expect } from 'chai'
import fs from 'fs'
import path from 'path'
import { registerCodeGraphTools } from '../../src/modules/mcp/code-graph.tools'

/**
 * MCP TOOLS/LIST SNAPSHOT
 * -----------------------
 * Agents and MCP clients read every tool's name and input schema from
 * tools/list. This builds the real /mcp server (the @pipeshub-ai/mcp package
 * tools plus the code-graph tools this app registers), lists the tools over an
 * in-memory transport and compares name and inputSchema with
 * mcp-tools-list.fixture.json, recorded on the zod 3 code base.
 *
 * Re-record when a tool or its schema changes ON PURPOSE:
 *   UPDATE_MCP_TOOLS_SNAPSHOT=1 TS_NODE_PROJECT=tsconfig.test.json \
 *     npx mocha --no-parallel --spec tests/contract/mcp-tools-list.test.ts
 * and explain the `git diff` of the fixture in the PR.
 */
const FIXTURE = path.join(__dirname, 'mcp-tools-list.fixture.json')
const nm = (p: string): string => path.join(__dirname, '../../node_modules', p)
// The mocha setup replaces the CJS view of these ESM packages with mocks, so
// load the real files with a native dynamic import.
const nativeImport = new Function('p', 'return import(p)') as (p: string) => Promise<any>

describe('MCP tools/list snapshot', function () {
  this.timeout(120000)

  it('lists the same tools with the same input schemas', async () => {
    const { createMCPServer } = await nativeImport(nm('@pipeshub-ai/mcp/esm/mcp-server/server.js'))
    const { Client } = await nativeImport(nm('@modelcontextprotocol/sdk/dist/esm/client/index.js'))
    const { InMemoryTransport } = await nativeImport(nm('@modelcontextprotocol/sdk/dist/esm/inMemory.js'))
    const noop = (): void => {}
    const { server } = createMCPServer({
      logger: { level: 'error', info: noop, debug: noop, warning: noop, error: noop },
      dynamic: false,
      serverURL: 'http://127.0.0.1:9/api/v1',
      getSDK: () => ({}),
    })
    registerCodeGraphTools(server, { aiBackend: 'http://127.0.0.1:9', authorization: 'Bearer x' } as any)
    const [a, b] = InMemoryTransport.createLinkedPair()
    await server.connect(a)
    const client = new Client({ name: 'contract', version: '1' })
    await client.connect(b)
    const list = await client.listTools()
    const tools = (list.tools as Array<{ name: string; inputSchema: unknown }>)
      .map((t) => ({ name: t.name, inputSchema: t.inputSchema }))
      .sort((x, y) => x.name.localeCompare(y.name))
    expect(tools.length).to.be.greaterThan(10)

    if (process.env.UPDATE_MCP_TOOLS_SNAPSHOT === '1') {
      fs.writeFileSync(FIXTURE, '[\n' + tools.map((t) => JSON.stringify(t)).join(',\n') + '\n]\n')
      return
    }
    const recorded = JSON.parse(fs.readFileSync(FIXTURE, 'utf8')) as typeof tools
    expect(tools.map((t) => t.name)).to.deep.equal(recorded.map((t) => t.name))
    for (const tool of tools) {
      const was = recorded.find((r) => r.name === tool.name)
      expect(tool.inputSchema, `${tool.name} inputSchema`).to.deep.equal(was?.inputSchema)
    }
  })
})
