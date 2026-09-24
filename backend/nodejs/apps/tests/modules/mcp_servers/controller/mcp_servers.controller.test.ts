import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as connectorUtils from '../../../../src/modules/tokens_manager/utils/connector.utils'
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
