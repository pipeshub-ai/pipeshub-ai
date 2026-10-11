import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as connectorUtils from '../../../../src/modules/tokens_manager/utils/connector.utils'
import { Logger } from '../../../../src/libs/services/logger.service'
import {
  listMcpCatalog,
  getMcpCatalogTemplate,
  listMcpInstances,
  createMcpInstance,
  getMcpInstance,
  updateMcpInstance,
  deleteMcpInstance,
  authenticateMcpInstance,
  updateMcpCredentials,
  removeMcpCredentials,
  autoAuthenticateMcpInstance,
  reauthenticateMcpInstance,
  getMcpOAuthAuthorizationUrl,
  handleMcpOAuthCallback,
  refreshMcpOAuthToken,
  getMcpOAuthConfig,
  updateMcpOAuthConfig,
  getMyMcpServers,
  getMcpInstanceTools,
  getAgentMcpServers,
  authenticateAgentMcpInstance,
  updateAgentMcpCredentials,
  removeAgentMcpCredentials,
  reauthenticateAgentMcpInstance,
  getAgentMcpOAuthAuthorizationUrl,
} from '../../../../src/modules/mcp_servers/controller/mcp_servers.controller'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function createMockRequest(overrides: Record<string, any> = {}): any {
  return {
    headers: { authorization: 'Bearer test-token' },
    body: {},
    params: {},
    query: {},
    user: { userId: 'user-1', orgId: 'org-1' },
    ...overrides,
  }
}

function createMockResponse(): any {
  const res: any = {
    status: sinon.stub(),
    json: sinon.stub(),
  }
  res.status.returns(res)
  res.json.returns(res)
  return res
}

function createMockAppConfig(): any {
  return {
    connectorBackend: 'http://localhost:8088',
  }
}

describe('mcp_servers/controller/mcp_servers.controller', () => {
  let executeStub: sinon.SinonStub
  let handleResponseStub: sinon.SinonStub

  beforeEach(() => {
    executeStub = sinon.stub(connectorUtils, 'executeConnectorCommand')
    handleResponseStub = sinon.stub(connectorUtils, 'handleConnectorResponse')
    sinon.stub(connectorUtils, 'handleBackendError').callsFake((err: any) => err)
  })

  afterEach(() => {
    sinon.restore()
  })

  // -------------------------------------------------------------------------
  // Shared behaviour across every proxied handler
  // -------------------------------------------------------------------------
  const handlers: Array<{
    name: string
    factory: (appConfig: any) => any
    expectedPath: string
    expectedMethod: string
    params?: Record<string, string>
    hasBody?: boolean
  }> = [
    { name: 'listMcpCatalog', factory: listMcpCatalog, expectedPath: '/api/v1/mcp-servers/catalog', expectedMethod: 'GET' },
    {
      name: 'getMcpCatalogTemplate',
      factory: getMcpCatalogTemplate,
      expectedPath: '/api/v1/mcp-servers/catalog/brave-search',
      expectedMethod: 'GET',
      params: { typeId: 'brave-search' },
    },
    { name: 'listMcpInstances', factory: listMcpInstances, expectedPath: '/api/v1/mcp-servers/instances', expectedMethod: 'GET' },
    {
      name: 'createMcpInstance',
      factory: createMcpInstance,
      expectedPath: '/api/v1/mcp-servers/instances',
      expectedMethod: 'POST',
      hasBody: true,
    },
    {
      name: 'getMcpInstance',
      factory: getMcpInstance,
      expectedPath: '/api/v1/mcp-servers/instances/inst-1',
      expectedMethod: 'GET',
      params: { instanceId: 'inst-1' },
    },
    {
      name: 'updateMcpInstance',
      factory: updateMcpInstance,
      expectedPath: '/api/v1/mcp-servers/instances/inst-1',
      expectedMethod: 'PUT',
      params: { instanceId: 'inst-1' },
      hasBody: true,
    },
    {
      name: 'deleteMcpInstance',
      factory: deleteMcpInstance,
      expectedPath: '/api/v1/mcp-servers/instances/inst-1',
      expectedMethod: 'DELETE',
      params: { instanceId: 'inst-1' },
    },
    {
      name: 'authenticateMcpInstance',
      factory: authenticateMcpInstance,
      expectedPath: '/api/v1/mcp-servers/instances/inst-1/authenticate',
      expectedMethod: 'POST',
      params: { instanceId: 'inst-1' },
      hasBody: true,
    },
    {
      name: 'updateMcpCredentials',
      factory: updateMcpCredentials,
      expectedPath: '/api/v1/mcp-servers/instances/inst-1/credentials',
      expectedMethod: 'PUT',
      params: { instanceId: 'inst-1' },
      hasBody: true,
    },
    {
      name: 'removeMcpCredentials',
      factory: removeMcpCredentials,
      expectedPath: '/api/v1/mcp-servers/instances/inst-1/credentials',
      expectedMethod: 'DELETE',
      params: { instanceId: 'inst-1' },
    },
    {
      name: 'autoAuthenticateMcpInstance',
      factory: autoAuthenticateMcpInstance,
      expectedPath: '/api/v1/mcp-servers/instances/inst-1/auto-authenticate',
      expectedMethod: 'POST',
      params: { instanceId: 'inst-1' },
    },
    {
      name: 'reauthenticateMcpInstance',
      factory: reauthenticateMcpInstance,
      expectedPath: '/api/v1/mcp-servers/instances/inst-1/reauthenticate',
      expectedMethod: 'POST',
      params: { instanceId: 'inst-1' },
    },
    {
      name: 'refreshMcpOAuthToken',
      factory: refreshMcpOAuthToken,
      expectedPath: '/api/v1/mcp-servers/instances/inst-1/oauth/refresh',
      expectedMethod: 'POST',
      params: { instanceId: 'inst-1' },
    },
    {
      name: 'getMcpOAuthConfig',
      factory: getMcpOAuthConfig,
      expectedPath: '/api/v1/mcp-servers/instances/inst-1/oauth-config',
      expectedMethod: 'GET',
      params: { instanceId: 'inst-1' },
    },
    {
      name: 'updateMcpOAuthConfig',
      factory: updateMcpOAuthConfig,
      expectedPath: '/api/v1/mcp-servers/instances/inst-1/oauth-config',
      expectedMethod: 'PUT',
      params: { instanceId: 'inst-1' },
      hasBody: true,
    },
    {
      name: 'getMcpInstanceTools',
      factory: getMcpInstanceTools,
      expectedPath: '/api/v1/mcp-servers/instances/inst-1/tools',
      expectedMethod: 'GET',
      params: { instanceId: 'inst-1' },
    },
    {
      name: 'getAgentMcpServers',
      factory: getAgentMcpServers,
      expectedPath: '/api/v1/mcp-servers/agents/agent-1',
      expectedMethod: 'GET',
      params: { agentKey: 'agent-1' },
    },
    {
      name: 'authenticateAgentMcpInstance',
      factory: authenticateAgentMcpInstance,
      expectedPath: '/api/v1/mcp-servers/agents/agent-1/instances/inst-1/authenticate',
      expectedMethod: 'POST',
      params: { agentKey: 'agent-1', instanceId: 'inst-1' },
      hasBody: true,
    },
    {
      name: 'updateAgentMcpCredentials',
      factory: updateAgentMcpCredentials,
      expectedPath: '/api/v1/mcp-servers/agents/agent-1/instances/inst-1/credentials',
      expectedMethod: 'PUT',
      params: { agentKey: 'agent-1', instanceId: 'inst-1' },
      hasBody: true,
    },
    {
      name: 'removeAgentMcpCredentials',
      factory: removeAgentMcpCredentials,
      expectedPath: '/api/v1/mcp-servers/agents/agent-1/instances/inst-1/credentials',
      expectedMethod: 'DELETE',
      params: { agentKey: 'agent-1', instanceId: 'inst-1' },
    },
    {
      name: 'reauthenticateAgentMcpInstance',
      factory: reauthenticateAgentMcpInstance,
      expectedPath: '/api/v1/mcp-servers/agents/agent-1/instances/inst-1/reauthenticate',
      expectedMethod: 'POST',
      params: { agentKey: 'agent-1', instanceId: 'inst-1' },
    },
    {
      name: 'getAgentMcpOAuthAuthorizationUrl',
      factory: getAgentMcpOAuthAuthorizationUrl,
      expectedPath: '/api/v1/mcp-servers/agents/agent-1/instances/inst-1/oauth/authorize',
      expectedMethod: 'GET',
      params: { agentKey: 'agent-1', instanceId: 'inst-1' },
    },
  ]

  for (const { name, factory, expectedPath, expectedMethod, params, hasBody } of handlers) {
    describe(name, () => {
      it('should return a handler function', () => {
        const handler = factory(createMockAppConfig())
        expect(handler).to.be.a('function')
      })

      it('should call next with UnauthorizedError when userId is missing', async () => {
        const handler = factory(createMockAppConfig())
        const req = createMockRequest({ user: {}, params })
        const res = createMockResponse()
        const next = sinon.stub()

        await handler(req, res, next)

        expect(next.calledOnce).to.be.true
        expect(executeStub.called).to.be.false
      })

      it(`should forward ${expectedMethod} to ${expectedPath}`, async () => {
        executeStub.resolves({ statusCode: 200, data: { success: true } })
        const handler = factory(createMockAppConfig())
        const body = hasBody ? { some: 'payload' } : undefined
        const req = createMockRequest({ params, body })
        const res = createMockResponse()
        const next = sinon.stub()

        await handler(req, res, next)

        expect(executeStub.calledOnce).to.be.true
        const [url, method, headers, forwardedBody] = executeStub.firstCall.args
        expect(url).to.equal(`http://localhost:8088${expectedPath}`)
        expect(method).to.equal(expectedMethod)
        expect(headers).to.deep.include(req.headers)
        if (hasBody) {
          expect(forwardedBody).to.deep.equal(body)
        } else {
          expect(forwardedBody).to.be.undefined
        }
        expect(handleResponseStub.calledOnce).to.be.true
      })

      it('should call next with the mapped error when the backend call fails', async () => {
        const error = new Error('upstream unreachable')
        executeStub.rejects(error)
        const handler = factory(createMockAppConfig())
        const req = createMockRequest({ params })
        const res = createMockResponse()
        const next = sinon.stub()

        await handler(req, res, next)

        expect(next.calledOnce).to.be.true
        expect(next.firstCall.args[0]).to.equal(error)
      })
    })
  }

  // -------------------------------------------------------------------------
  // Query-string forwarding (handlers with non-trivial query params)
  // -------------------------------------------------------------------------
  describe('listMcpCatalog query params', () => {
    it('should forward page, limit, and search', async () => {
      executeStub.resolves({ statusCode: 200, data: { templates: [] } })
      const handler = listMcpCatalog(createMockAppConfig())
      const req = createMockRequest({ query: { page: '2', limit: '10', search: 'jira' } })
      const res = createMockResponse()
      const next = sinon.stub()

      await handler(req, res, next)

      const url: string = executeStub.firstCall.args[0]
      expect(url).to.include('page=2')
      expect(url).to.include('limit=10')
      expect(url).to.include('search=jira')
    })

    it('should omit empty/undefined query params', async () => {
      executeStub.resolves({ statusCode: 200, data: { templates: [] } })
      const handler = listMcpCatalog(createMockAppConfig())
      const req = createMockRequest({ query: {} })
      const res = createMockResponse()
      const next = sinon.stub()

      await handler(req, res, next)

      const url: string = executeStub.firstCall.args[0]
      expect(url).to.equal('http://localhost:8088/api/v1/mcp-servers/catalog')
    })
  })

  describe('resends and timeouts', () => {
    async function optionsFor(factory: (appConfig: any) => any, method: string): Promise<Record<string, unknown>> {
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = factory(createMockAppConfig())
      await handler(createMockRequest({ method, params: { instanceId: 'inst-1', agentKey: 'a1' }, query: {} }), createMockResponse(), sinon.stub())
      return executeStub.lastCall.args[4]
    }

    it('bounds every call and resends only plain reads', async () => {
      expect(await optionsFor(listMcpInstances, 'GET')).to.deep.equal({ timeoutMs: 90_000, retries: 3 })
      expect(await optionsFor(createMcpInstance, 'POST')).to.deep.equal({ timeoutMs: 90_000, retries: 1 })
      expect(await optionsFor(deleteMcpInstance, 'DELETE')).to.deep.equal({ timeoutMs: 90_000, retries: 1 })
    })

    it('never resends a read that changes OAuth state', async () => {
      expect((await optionsFor(handleMcpOAuthCallback, 'GET')).retries).to.equal(1)
      expect((await optionsFor(getMcpOAuthAuthorizationUrl, 'GET')).retries).to.equal(1)
      expect((await optionsFor(getAgentMcpOAuthAuthorizationUrl, 'GET')).retries).to.equal(1)
    })
  })

  describe('listMcpInstances query params', () => {
    it('should forward includePersonal', async () => {
      executeStub.resolves({ statusCode: 200, data: { instances: [] } })
      const handler = listMcpInstances(createMockAppConfig())
      const req = createMockRequest({ query: { includePersonal: 'true', orgId: 'other-org' } })
      const res = createMockResponse()
      const next = sinon.stub()

      await handler(req, res, next)

      const url: string = executeStub.firstCall.args[0]
      expect(url).to.include('includePersonal=true')
      expect(url).to.not.include('orgId')
    })

    it('forwards includePersonal and reveal together', async () => {
      executeStub.resolves({ statusCode: 200, data: { instances: [] } })
      const req = createMockRequest({ query: { includePersonal: 'true', reveal: 'true' } })

      await listMcpInstances(createMockAppConfig())(req, createMockResponse(), sinon.stub())

      expect(executeStub.firstCall.args[0]).to.equal(
        'http://localhost:8088/api/v1/mcp-servers/instances?includePersonal=true&reveal=true',
      )
    })
  })

  describe('getMcpInstanceTools query params', () => {
    it('should forward cached, and nothing else', async () => {
      executeStub.resolves({ statusCode: 200, data: { tools: [] } })
      const handler = getMcpInstanceTools(createMockAppConfig())
      const req = createMockRequest({ params: { instanceId: 'inst-1' }, query: { cached: 'true', other: 'x' } })
      const res = createMockResponse()
      const next = sinon.stub()

      await handler(req, res, next)

      const url: string = executeStub.firstCall.args[0]
      expect(url).to.match(/\/instances\/inst-1\/tools\?cached=true$/)
    })
  })

  describe('getMyMcpServers query params', () => {
    it('should forward includeTools', async () => {
      executeStub.resolves({ statusCode: 200, data: { instances: [] } })
      const handler = getMyMcpServers(createMockAppConfig())
      const req = createMockRequest({ query: { includeTools: 'false' } })
      const res = createMockResponse()
      const next = sinon.stub()

      await handler(req, res, next)

      const url: string = executeStub.firstCall.args[0]
      expect(url).to.include('includeTools=false')
    })
  })

  describe('getMcpOAuthAuthorizationUrl query params', () => {
    it('should forward baseUrl', async () => {
      executeStub.resolves({ statusCode: 200, data: { authorizationUrl: 'https://oauth.example.com' } })
      const handler = getMcpOAuthAuthorizationUrl(createMockAppConfig())
      const req = createMockRequest({
        params: { instanceId: 'inst-1' },
        query: { baseUrl: 'https://app.example.com' },
      })
      const res = createMockResponse()
      const next = sinon.stub()

      await handler(req, res, next)

      const url: string = executeStub.firstCall.args[0]
      expect(url).to.include('/instances/inst-1/oauth/authorize')
      expect(url).to.include('baseUrl=https%3A%2F%2Fapp.example.com')
    })
  })

  describe('getAgentMcpServers query params', () => {
    it('should forward includeTools', async () => {
      executeStub.resolves({ statusCode: 200, data: { instances: [] } })
      const handler = getAgentMcpServers(createMockAppConfig())
      const req = createMockRequest({ params: { agentKey: 'agent-1' }, query: { includeTools: 'false' } })
      const res = createMockResponse()
      const next = sinon.stub()

      await handler(req, res, next)

      const url: string = executeStub.firstCall.args[0]
      expect(url).to.include('/agents/agent-1')
      expect(url).to.include('includeTools=false')
    })
  })

  describe('getAgentMcpOAuthAuthorizationUrl query params', () => {
    it('should forward baseUrl', async () => {
      executeStub.resolves({ statusCode: 200, data: { authorizationUrl: 'https://oauth.example.com' } })
      const handler = getAgentMcpOAuthAuthorizationUrl(createMockAppConfig())
      const req = createMockRequest({
        params: { agentKey: 'agent-1', instanceId: 'inst-1' },
        query: { baseUrl: 'https://app.example.com' },
      })
      const res = createMockResponse()
      const next = sinon.stub()

      await handler(req, res, next)

      const url: string = executeStub.firstCall.args[0]
      expect(url).to.include('/agents/agent-1/instances/inst-1/oauth/authorize')
      expect(url).to.include('baseUrl=https%3A%2F%2Fapp.example.com')
    })
  })

  describe('reveal forwarding', () => {
    const revealHandlers = [
      { name: 'listMcpInstances', factory: listMcpInstances, path: '/api/v1/mcp-servers/instances' },
      {
        name: 'getMcpOAuthConfig',
        factory: getMcpOAuthConfig,
        path: '/api/v1/mcp-servers/instances/inst-1/oauth-config',
      },
    ]

    for (const { name, factory, path } of revealHandlers) {
      it(`${name} forwards reveal=true`, async () => {
        executeStub.resolves({ statusCode: 200, data: {} })
        const req = createMockRequest({ params: { instanceId: 'inst-1' }, query: { reveal: 'true' } })

        await factory(createMockAppConfig())(req, createMockResponse(), sinon.stub())

        expect(executeStub.firstCall.args[0]).to.equal(`http://localhost:8088${path}?reveal=true`)
      })

      for (const reveal of [['true'], ['false', 'true'], 'TRUE', '1', { a: 'true' }]) {
        it(`${name} drops reveal=${JSON.stringify(reveal)}`, async () => {
          executeStub.resolves({ statusCode: 200, data: {} })
          const req = createMockRequest({ params: { instanceId: 'inst-1' }, query: { reveal } })

          await factory(createMockAppConfig())(req, createMockResponse(), sinon.stub())

          expect(executeStub.firstCall.args[0]).to.equal(`http://localhost:8088${path}`)
        })
      }

      it(`${name} drops reveal for OAuth-app tokens`, async () => {
        executeStub.resolves({ statusCode: 200, data: {} })
        const req = createMockRequest({
          params: { instanceId: 'inst-1' },
          query: { reveal: 'true' },
          user: { userId: 'user-1', orgId: 'org-1', isOAuth: true },
        })

        await factory(createMockAppConfig())(req, createMockResponse(), sinon.stub())

        expect(executeStub.firstCall.args[0]).to.equal(`http://localhost:8088${path}`)
      })
    }
  })

  describe('handleMcpOAuthCallback', () => {
    it('should forward code, state, and error query params without requiring instanceId', async () => {
      executeStub.resolves({ statusCode: 200, data: { success: true } })
      const handler = handleMcpOAuthCallback(createMockAppConfig())
      const req = createMockRequest({ query: { code: 'abc', state: 'xyz' } })
      const res = createMockResponse()
      const next = sinon.stub()

      await handler(req, res, next)

      const url: string = executeStub.firstCall.args[0]
      expect(url).to.equal('http://localhost:8088/api/v1/mcp-servers/oauth/callback?code=abc&state=xyz')
    })

    it('logs the callback without its code or state', async () => {
      executeStub.resolves({ statusCode: 200, data: { success: true } })
      // The controller takes its logger once, at load, and under mocha's parallel workers
      // another file may have loaded it with `Logger.getInstance` stubbed: load a private copy.
      const debug = sinon.stub()
      const recorder = { debug, info: sinon.stub(), warn: sinon.stub(), error: sinon.stub() } as unknown as Logger
      const modulePath = require.resolve('../../../../src/modules/mcp_servers/controller/mcp_servers.controller')
      const original = require.cache[modulePath]
      delete require.cache[modulePath]
      const getInstance = sinon.stub(Logger, 'getInstance').returns(recorder)
      let privateCopy: { handleMcpOAuthCallback: typeof handleMcpOAuthCallback }
      try {
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        privateCopy = require(modulePath)
      } finally {
        getInstance.restore()
        if (original) require.cache[modulePath] = original
        else delete require.cache[modulePath]
      }
      const handler = privateCopy.handleMcpOAuthCallback(createMockAppConfig())
      const req = createMockRequest({ query: { code: 'code-secret', state: 'state-secret' } })

      await handler(req, createMockResponse(), sinon.stub())

      const logged = JSON.stringify(debug.args)
      expect(logged).to.contain('/oauth/callback')
      expect(logged).to.not.contain('code-secret')
      expect(logged).to.not.contain('state-secret')
      // The Python service still gets both.
      expect(executeStub.firstCall.args[0]).to.contain('code=code-secret&state=state-secret')
    })

    it('should forward provider error param', async () => {
      executeStub.resolves({ statusCode: 200, data: { success: false, error: 'access_denied' } })
      const handler = handleMcpOAuthCallback(createMockAppConfig())
      const req = createMockRequest({ query: { error: 'access_denied' } })
      const res = createMockResponse()
      const next = sinon.stub()

      await handler(req, res, next)

      const url: string = executeStub.firstCall.args[0]
      expect(url).to.include('error=access_denied')
    })
  })
})
