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
import { McpToolCatalog } from '../../../src/modules/mcp/tool-catalog'
import { makeOrgContext, runWithOrgContext } from '../../../src/libs/context/orgContext'

const mcpServerExports = require.cache[
  require.resolve('@pipeshub-ai/mcp/esm/mcp-server/server.js')
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
        return { server: { _registeredTools: tools, registerTool: (name: string) => { tools[name] = { remove() {} } } } }
      })
    })

    afterEach(() => {
      mcpServerExports.createMCPServer = origCreate
      McpToolCatalog.reset()
    })

    it("lists the package's tools and the code tools, and marks the ones the read scope leaves out as writing", async () => {
      const tools = await McpToolCatalog.list()
      expect(tools.find((t) => t.name === 'pipeshub_search')).to.deep.equal({ name: 'pipeshub_search', title: 'Semantic search', writes: false })
      expect(tools.find((t) => t.name === 'pipeshub_upload')).to.deep.equal({ name: 'pipeshub_upload', title: 'Upload', writes: true })
      expect(tools.find((t) => t.name === 'pipeshub_code_read')).to.include({ writes: false })
      const isWrite = await McpToolCatalog.isWriteTool()
      expect(isWrite('pipeshub_upload')).to.equal(true)
      expect(isWrite('pipeshub_search')).to.equal(false)
      expect(mcpServerExports.createMCPServer.callCount, 'built once').to.equal(2)
    })
  })
})
