import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { createMockNext, createMockRequest, createMockResponse } from '../../helpers/mock-request'
import {
  DEFAULT_MCP_ACCESS_POLICY,
  MCP_ACCESS_POLICY_TTL_MS,
  McpAccessPolicyStore,
  mcpAccessGuard,
  mcpAccessPolicySchema,
  toolTurnedOff,
} from '../../../src/modules/mcp/access-policy'
import { McpToolCatalog, toolArguments } from '../../../src/modules/mcp/tool-catalog'
import { z } from 'zod'
import Ajv from 'ajv'
import yaml from 'js-yaml'
import { readFileSync } from 'fs'
import path from 'path'
import { makeOrgContext, runWithOrgContext } from '../../../src/libs/context/orgContext'

const mcpServerExports = require.cache[
  require.resolve('@pipeshub-ai/mcp/esm/mcp-server/server.js')
]!.exports
const oauthScopesExports = require.cache[
  require.resolve('@pipeshub-ai/mcp/esm/mcp-server/oauth-scopes.js')
]!.exports

/** A key-value store in memory, shaped like the slice the policy store uses. */
function memoryStore(connected = true) {
  const data = new Map<string, string>()
  return {
    data,
    isConnected: () => connected,
    get: sinon.stub().callsFake(async (key: string) => data.get(key) ?? null),
    set: sinon.stub().callsFake(async (key: string, value: string) => {
      data.set(key, value)
    }),
  }
}

describe('MCP access policy', () => {
  let store: ReturnType<typeof memoryStore>

  beforeEach(() => {
    McpAccessPolicyStore.resetCache()
    store = memoryStore()
    sinon.stub(McpAccessPolicyStore, 'store').callsFake(() => store as any)
  })

  afterEach(() => {
    sinon.restore()
    McpAccessPolicyStore.resetCache()
  })

  describe('store', () => {
    it('answers the defaults, which change nothing, for an org with nothing saved', async () => {
      expect(await McpAccessPolicyStore.read('org-a')).to.deep.equal({
        enabled: true, allowWriteTools: true, disabledTools: [],
      })
    })

    it("keeps each org's policy under its own key", async () => {
      await McpAccessPolicyStore.write('org-a', { enabled: false, allowWriteTools: false, disabledTools: ['pipeshub_upload'] })
      McpAccessPolicyStore.resetCache()
      expect((await McpAccessPolicyStore.read('org-a')).enabled).to.equal(false)
      expect(await McpAccessPolicyStore.read('org-b')).to.deep.equal(DEFAULT_MCP_ACCESS_POLICY)
      expect([...store.data.keys()]).to.deep.equal(['/services/mcp/accessPolicy/org-a'])
    })

    it('reads the store again once the cached policy is older than the TTL', async () => {
      const clock = sinon.useFakeTimers({ now: 1_000_000, toFake: ['Date'] })
      await McpAccessPolicyStore.read('org-a')
      store.data.set('/services/mcp/accessPolicy/org-a', JSON.stringify({ enabled: false, allowWriteTools: true, disabledTools: [] }))
      expect((await McpAccessPolicyStore.read('org-a')).enabled).to.equal(true)
      clock.tick(MCP_ACCESS_POLICY_TTL_MS)
      expect((await McpAccessPolicyStore.read('org-a')).enabled).to.equal(false)
    })

    it('keeps the last policy it read when a later read fails', async () => {
      const clock = sinon.useFakeTimers({ now: 1_000_000, toFake: ['Date'] })
      store.data.set('/services/mcp/accessPolicy/org-a', JSON.stringify({ enabled: false, allowWriteTools: true, disabledTools: [] }))
      await McpAccessPolicyStore.read('org-a')
      clock.tick(MCP_ACCESS_POLICY_TTL_MS)
      store.get.rejects(new Error('store down'))
      expect((await McpAccessPolicyStore.read('org-a')).enabled).to.equal(false)
    })

    it('refuses rather than serving a stale "on" when a later read fails', async () => {
      const clock = sinon.useFakeTimers({ now: 1_000_000, toFake: ['Date'] })
      await McpAccessPolicyStore.read('org-a')
      clock.tick(MCP_ACCESS_POLICY_TTL_MS)
      store.get.rejects(new Error('store down'))
      let refused: unknown
      await McpAccessPolicyStore.read('org-a').catch((error: unknown) => {
        refused = error
      })
      expect(refused).to.be.instanceOf(Error)
    })

    it('refuses rather than opening up when the first read fails', async () => {
      store.get.rejects(new Error('store down'))
      const error = await McpAccessPolicyStore.read('org-a').then(() => undefined, (e: Error) => e)
      expect(error?.message).to.contain('could not be read')
    })

    it('treats a stored value that does not parse as a failed read', async () => {
      store.data.set('/services/mcp/accessPolicy/org-a', '{"enabled":"nope"}')
      const error = await McpAccessPolicyStore.read('org-a').then(() => undefined, (e: Error) => e)
      expect(error).to.exist
    })
  })

  describe('schema', () => {
    it('sorts and dedupes the turned-off tools, and refuses unknown fields', () => {
      expect(mcpAccessPolicySchema.parse({ enabled: true, allowWriteTools: true, disabledTools: ['b_tool', 'a_tool', 'b_tool'] }).disabledTools)
        .to.deep.equal(['a_tool', 'b_tool'])
      expect(mcpAccessPolicySchema.safeParse({ enabled: true, allowWriteTools: true, disabledTools: [], extra: 1 }).success).to.equal(false)
      expect(mcpAccessPolicySchema.safeParse({ enabled: true, allowWriteTools: true, disabledTools: ['Not A Name'] }).success).to.equal(false)
    })
  })

  describe('guard', () => {
    const run = async (orgId: string | undefined) => {
      const req = createMockRequest({ user: { userId: 'u1', ...(orgId ? { orgId } : {}) } })
      const res: any = { ...createMockResponse(), locals: {} }
      res.status.returns(res)
      const next = createMockNext()
      await (orgId
        ? runWithOrgContext(makeOrgContext({ orgId, userId: 'u1', kind: 'user', source: 'jwt' }), () => mcpAccessGuard(req as any, res, next))
        : mcpAccessGuard(req as any, res, next))
      return { res, next }
    }

    it('answers a JSON-RPC 403 for an org whose admin turned MCP off', async () => {
      await McpAccessPolicyStore.write('org-off', { enabled: false, allowWriteTools: true, disabledTools: [] })
      const { res, next } = await run('org-off')
      expect(next.called).to.equal(false)
      expect(res.status.calledWith(403)).to.equal(true)
      expect(res.json.firstCall.args[0]).to.deep.equal({
        jsonrpc: '2.0',
        error: { code: -32000, message: 'MCP access is turned off for this organization by an admin.' },
        id: null,
      })
    })

    it('hands the policy on for an org with MCP on', async () => {
      await McpAccessPolicyStore.write('org-on', { enabled: true, allowWriteTools: false, disabledTools: [] })
      const { res, next } = await run('org-on')
      expect(next.calledOnceWithExactly()).to.equal(true)
      expect(res.locals.mcpAccessPolicy.allowWriteTools).to.equal(false)
    })

    it("falls back to the token's org when no org context is bound", async () => {
      await McpAccessPolicyStore.write('org-ctxless', { enabled: false, allowWriteTools: true, disabledTools: [] })
      const req = createMockRequest({ user: { userId: 'u1', orgId: 'org-ctxless' } })
      const res: any = { ...createMockResponse(), locals: {} }
      res.status.returns(res)
      const next = createMockNext()
      await mcpAccessGuard(req as any, res, next)
      expect(next.called).to.equal(false)
      expect(res.status.calledWith(403)).to.equal(true)
    })

    it('refuses rather than skipping the policy when no org is known', async () => {
      const { res, next } = await run(undefined)
      expect(next.called).to.equal(false)
      expect(res.status.calledWith(503)).to.equal(true)
    })

    it('answers 503 when the settings cannot be read', async () => {
      store.get.rejects(new Error('store down'))
      const { res, next } = await run('org-a')
      expect(next.called).to.equal(false)
      expect(res.status.calledWith(503)).to.equal(true)
    })
  })

  describe('toolTurnedOff', () => {
    const call = (name: string) => ({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name } })
    const writes = async () => (name: string) => name === 'pipeshub_upload'

    it('names a turned-off tool, also inside a batch', async () => {
      const policy = { enabled: true, allowWriteTools: true, disabledTools: ['pipeshub_search'] }
      expect(await toolTurnedOff(call('pipeshub_search'), policy, writes)).to.equal('pipeshub_search')
      expect(await toolTurnedOff([call('pipeshub_sources'), call('pipeshub_search')], policy, writes)).to.equal('pipeshub_search')
      expect(await toolTurnedOff(call('pipeshub_sources'), policy, writes)).to.equal(undefined)
    })

    it('names a write tool only while write tools are off, and asks the catalog only then', async () => {
      const catalog = sinon.spy(writes)
      expect(await toolTurnedOff(call('pipeshub_upload'), DEFAULT_MCP_ACCESS_POLICY, catalog)).to.equal(undefined)
      expect(catalog.called).to.equal(false)
      const noWrites = { enabled: true, allowWriteTools: false, disabledTools: [] }
      expect(await toolTurnedOff(call('pipeshub_upload'), noWrites, catalog)).to.equal('pipeshub_upload')
      expect(await toolTurnedOff({ jsonrpc: '2.0', id: 1, method: 'tools/list' }, noWrites, catalog)).to.equal(undefined)
      expect(catalog.calledOnce).to.equal(true)
    })
  })

  describe('tool catalog', () => {
    let origCreate: any

    beforeEach(() => {
      McpToolCatalog.reset()
      origCreate = mcpServerExports.createMCPServer
      // The package leaves the write tools out when it is given the `read` scope.
      mcpServerExports.createMCPServer = sinon.stub().callsFake((opts: any) => {
        const tools: Record<string, unknown> = {
          pipeshub_search: { title: 'Semantic search', remove() {} },
          ...(opts.scopes ? {} : { pipeshub_upload: { annotations: { title: 'Upload' }, remove() {} } }),
        }
        // Kept as the SDK keeps it: the catalog reads a tool's description, schema and hints from it.
        return { server: { _registeredTools: tools, registerTool: (name: string, config: object) => { tools[name] = { ...config, remove() {} } } } }
      })
    })

    afterEach(() => {
      mcpServerExports.createMCPServer = origCreate
      McpToolCatalog.reset()
    })

    it("lists the package's tools and the code tools, and marks the ones the read scope leaves out as writing", async () => {
      const tools = await McpToolCatalog.list()
      const blank = { description: '', arguments: [], hints: { readOnly: false, destructive: false, idempotent: false, openWorld: false }, scopes: [] }
      expect(tools.find((t) => t.name === 'pipeshub_search')).to.deep.equal({ name: 'pipeshub_search', title: 'Semantic search', writes: false, ...blank })
      expect(tools.find((t) => t.name === 'pipeshub_upload')).to.deep.equal({ name: 'pipeshub_upload', title: 'Upload', writes: true, ...blank })
      expect(tools.find((t) => t.name === 'pipeshub_code_read')).to.include({ writes: false })
      const isWrite = await McpToolCatalog.isWriteTool()
      expect(isWrite('pipeshub_upload')).to.equal(true)
      expect(isWrite('pipeshub_search')).to.equal(false)
      expect(mcpServerExports.createMCPServer.callCount, 'built once').to.equal(2)
    })

    it('gives the code and entity tools the scopes the MCP endpoint checks for them, with their arguments', async () => {
      const tools = await McpToolCatalog.list()
      const code = tools.find((t) => t.name === 'pipeshub_code_read')!
      expect(code.scopes).to.deep.equal(['code:read'])
      expect(code.hints.readOnly).to.equal(true)
      expect(code.arguments.map((a) => a.name)).to.include('repo')
      const entities = tools.find((t) => t.name === 'pipeshub_entities')!
      expect(entities.scopes).to.deep.equal(['semantic:read'])
      expect(entities.arguments.find((a) => a.name === 'action')).to.include({ type: 'enum', required: true })
      expect(entities.description).to.not.equal('')
    })

    it("takes a package tool's scopes from the package", async () => {
      const original = oauthScopesExports.requiredOAuthScopes
      oauthScopesExports.requiredOAuthScopes = (tool: string) => ({ pipeshub_upload: ['kb:upload'] } as Record<string, string[]>)[tool]
      try {
        const tools = await McpToolCatalog.list()
        expect(tools.find((t) => t.name === 'pipeshub_upload')!.scopes).to.deep.equal(['kb:upload'])
        expect(tools.find((t) => t.name === 'pipeshub_search')!.scopes).to.deep.equal([])
      } finally {
        oauthScopesExports.requiredOAuthScopes = original
      }
    })
  })

  describe('tool arguments', () => {
    it('reads names, types, required, descriptions and allowed values off the input schema', () => {
      const schema = z.object({
        query: z.string().describe('What to look for.'),
        limit: z.number().int().optional(),
        mode: z.enum(['content', 'thread']).optional(),
        kinds: z.array(z.enum(['date', 'currency'])).optional(),
        cursor: z.string().nullable().optional(),
        filters: z.object({ apps: z.array(z.string()) }).optional(),
        mentions: z.array(z.object({ name: z.string() })),
      })
      expect(toolArguments(schema)).to.deep.equal([
        { name: 'query', type: 'string', required: true, description: 'What to look for.' },
        { name: 'limit', type: 'integer', required: false },
        { name: 'mode', type: 'enum', required: false, values: ['content', 'thread'] },
        { name: 'kinds', type: 'enum[]', required: false, values: ['date', 'currency'] },
        { name: 'cursor', type: 'string', required: false },
        { name: 'filters', type: 'object', required: false },
        { name: 'mentions', type: 'object[]', required: true },
      ])
    })

    it('lists nothing for a tool without input, or one whose schema cannot be read', () => {
      expect(toolArguments(undefined)).to.deep.equal([])
      expect(toolArguments({ not: 'a schema' })).to.deep.equal([])
    })
  })

  describe('tool catalog, with the real @pipeshub-ai/mcp package', () => {
    const nm = (p: string): string => path.join(__dirname, '../../../node_modules', p)
    // The mocha setup mocks the CJS view of the package; load the real ESM files.
    // eslint-disable-next-line @typescript-eslint/no-implied-eval -- ts-node compiles import() to the mocked require()
    const nativeImport = new Function('p', 'return import(p)') as (p: string) => Promise<any>
    let origCreate: any
    let origScopes: any

    beforeEach(async () => {
      McpToolCatalog.reset()
      origCreate = mcpServerExports.createMCPServer
      origScopes = oauthScopesExports.requiredOAuthScopes
      mcpServerExports.createMCPServer = (await nativeImport(nm('@pipeshub-ai/mcp/esm/mcp-server/server.js'))).createMCPServer
      oauthScopesExports.requiredOAuthScopes = (await nativeImport(nm('@pipeshub-ai/mcp/esm/mcp-server/oauth-scopes.js'))).requiredOAuthScopes
    })

    afterEach(() => {
      mcpServerExports.createMCPServer = origCreate
      oauthScopesExports.requiredOAuthScopes = origScopes
      McpToolCatalog.reset()
    })

    it('answers every tool in the shape the published spec documents', async () => {
      const spec = yaml.load(readFileSync(path.join(__dirname, '../../../src/modules/api-docs/pipeshub-openapi.yaml'), 'utf8')) as any
      const ajv = new Ajv({ strict: false })
      ajv.addSchema({ components: { schemas: spec.components.schemas } }, 'spec')
      const validate = ajv.compile({ $ref: 'spec#/components/schemas/McpAccessTool' })
      const tools = await McpToolCatalog.list()
      expect(tools.length).to.be.greaterThan(20)
      for (const tool of tools) {
        expect(validate(tool), `${tool.name}: ${JSON.stringify(validate.errors)}`).to.equal(true)
        expect(tool.description, tool.name).to.not.equal('')
      }
    })

    it("carries each tool's own hints, arguments and scopes", async () => {
      const tools = await McpToolCatalog.list()
      const byName = (name: string) => tools.find((t) => t.name === name)!
      expect(byName('pipeshub_upload')).to.deep.include({ writes: true, scopes: ['kb:upload'] })
      expect(byName('pipeshub_upload').hints.readOnly).to.equal(false)
      expect(byName('pipeshub_upload').arguments.find((a) => a.name === 'collectionId')).to.include({ required: true, type: 'string' })
      expect(byName('pipeshub_search').hints).to.include({ readOnly: true, openWorld: true })
      expect(byName('pipeshub_search').scopes).to.deep.equal(['semantic:write'])
      expect(byName('pipeshub_chat').hints.destructive).to.equal(true)
      expect(byName('pipeshub_connect_source').scopes).to.deep.equal([])
      expect(byName('pipeshub_get_record_content').arguments.find((a) => a.name === 'mode')!.values).to.include.members(['content', 'lookup'])
    })
  })
})
