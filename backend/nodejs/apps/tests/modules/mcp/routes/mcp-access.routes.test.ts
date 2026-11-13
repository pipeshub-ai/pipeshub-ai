import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import express from 'express'
import { AddressInfo } from 'net'
import { readFileSync } from 'fs'
import { join } from 'path'
import yaml from 'js-yaml'
import { Container } from 'inversify'
import { createConfigurationManagerRouter } from '../../../../src/modules/configuration_manager/routes/cm_routes'
import { McpAccessPolicyStore } from '../../../../src/modules/mcp/access-policy'
import { McpToolCatalog } from '../../../../src/modules/mcp/tool-catalog'
import { BaseError } from '../../../../src/libs/errors/base.error'
import * as userAdminService from '../../../../src/modules/user_management/services/user-admin.service'
import { makeOrgContext, runWithOrgContext } from '../../../../src/libs/context/orgContext'

const SPEC = join(__dirname, '..', '..', '..', '..', 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml')

// eslint-disable-next-line @typescript-eslint/no-unused-vars -- express treats only a four-argument handler as an error handler
const sendError: express.ErrorRequestHandler = (err, _req, res, _next) => {
  res.status(err instanceof BaseError ? err.statusCode : 500).json({ error: err.message })
}

describe('MCP access settings routes', () => {
  let caller: Record<string, unknown>
  let data: Map<string, string>
  let admins: Set<string>

  const serve = async <T>(fn: (url: string) => Promise<T>): Promise<T> => {
    const app = express()
    app.use(express.json())
    const container = new Container()
    const events = { start: sinon.stub().resolves(), publishEvent: sinon.stub().resolves(), stop: sinon.stub().resolves() }
    container.bind('KeyValueStoreService').toConstantValue({} as any)
    container.bind('AppConfig').toConstantValue({ scopedJwtSecret: 's', storage: {} } as any)
    container.bind('EntitiesEventProducer').toConstantValue(events as any)
    container.bind('AiConfigEventProducer').toConstantValue(events as any)
    container.bind('SyncEventProducer').toConstantValue(events as any)
    container.bind('ConfigService').toConstantValue({} as any)
    container.bind('AuthMiddleware').toConstantValue({
      // As the real middleware does: the caller's org is bound for the rest of the request.
      authenticate(req: any, _res: any, next: any) {
        req.user = caller
        runWithOrgContext(
          makeOrgContext({ orgId: String(caller.orgId), userId: String(caller.userId), kind: 'user', source: 'jwt' }),
          () => next(),
        )
      },
      scopedTokenValidator: () => (_req: any, _res: any, next: any) => next(),
    } as any)
    app.use('/api/v1/configurationManager', createConfigurationManagerRouter(container))
    app.use(sendError)
    const server = app.listen(0)
    try {
      const { port } = server.address() as AddressInfo
      return await fn(`http://127.0.0.1:${port}/api/v1/configurationManager/mcp`)
    } finally {
      server.close()
    }
  }
  const put = (url: string, body: unknown) =>
    fetch(`${url}/access`, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })

  beforeEach(() => {
    McpAccessPolicyStore.resetCache()
    data = new Map()
    admins = new Set(['admin-a:org-a', 'admin-b:org-b'])
    caller = { userId: 'admin-a', orgId: 'org-a' }
    sinon.stub(McpAccessPolicyStore, 'store').returns({
      isConnected: () => true,
      get: async (key: string) => data.get(key) ?? null,
      set: async (key: string, value: string) => {
        data.set(key, value)
      },
    } as any)
    sinon.stub(userAdminService, 'isUserOrgAdmin').callsFake(async (userId: string, orgId: string) => admins.has(`${userId}:${orgId}`))
  })

  afterEach(() => {
    sinon.restore()
    McpAccessPolicyStore.resetCache()
  })

  it("answers the defaults, then what the caller's org saved, and never another org's", async () => {
    await serve(async (url) => {
      expect(await (await fetch(`${url}/access`)).json()).to.deep.equal({ enabled: true, allowWriteTools: true, disabledTools: [] })

      const saved = await put(url, { enabled: false, allowWriteTools: false, disabledTools: ['pipeshub_upload'] })
      expect(saved.status).to.equal(200)
      expect(await (await fetch(`${url}/access`)).json()).to.deep.include({ enabled: false })

      caller = { userId: 'admin-b', orgId: 'org-b' }
      expect(await (await fetch(`${url}/access`)).json()).to.deep.equal({ enabled: true, allowWriteTools: true, disabledTools: [] })
    })
    expect([...data.keys()]).to.deep.equal(['/services/mcp/accessPolicy/org-a'])
  })

  it('refuses a member who is not an admin, for reading and for saving', async () => {
    caller = { userId: 'member', orgId: 'org-a' }
    await serve(async (url) => {
      expect((await fetch(`${url}/access`)).status).to.equal(403)
      expect((await put(url, { enabled: false, allowWriteTools: true, disabledTools: [] })).status).to.equal(403)
      expect((await fetch(`${url}/access/tools`)).status).to.equal(403)
    })
    expect(data.size).to.equal(0)
  })

  it('needs config:write to save with an OAuth token', async () => {
    caller = { userId: 'admin-a', orgId: 'org-a', isOAuth: true, oauthScopes: ['config:read'] }
    await serve(async (url) => {
      expect((await fetch(`${url}/access`)).status).to.equal(200)
      expect((await put(url, { enabled: false, allowWriteTools: true, disabledTools: [] })).status).to.equal(403)
    })
  })

  it('refuses a body that is not a whole policy', async () => {
    await serve(async (url) => {
      expect((await put(url, { enabled: false })).status).to.equal(400)
      expect((await put(url, { enabled: true, allowWriteTools: true, disabledTools: ['<b>'] })).status).to.equal(400)
    })
    expect(data.size).to.equal(0)
  })

  it('lists the tools from the catalog', async () => {
    sinon.stub(McpToolCatalog, 'list').resolves([{ name: 'pipeshub_upload', title: 'Upload', writes: true }])
    await serve(async (url) => {
      expect(await (await fetch(`${url}/access/tools`)).json()).to.deep.equal({
        tools: [{ name: 'pipeshub_upload', title: 'Upload', writes: true }],
      })
    })
  })

  it('is in the published spec with the scopes it checks', () => {
    const spec = yaml.load(readFileSync(SPEC, 'utf8')) as {
      paths: Record<string, Record<string, { security?: Array<Record<string, string[]>> }>>
    }
    const scopes = (p: string, method: string) =>
      spec.paths[p]?.[method]?.security?.find((s) => s.oauth2)?.oauth2
    expect(scopes('/configurationManager/mcp/access', 'get')).to.deep.equal(['config:read'])
    expect(scopes('/configurationManager/mcp/access', 'put')).to.deep.equal(['config:write'])
    expect(scopes('/configurationManager/mcp/access/tools', 'get')).to.deep.equal(['config:read'])
  })
})
