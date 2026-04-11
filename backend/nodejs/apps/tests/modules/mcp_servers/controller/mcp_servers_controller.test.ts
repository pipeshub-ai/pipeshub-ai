import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as connectorUtils from '../../../../src/modules/tokens_manager/utils/connector.utils'
import {
  getCatalog,
  getCatalogItem,
  getInstances,
  createInstance,
  getInstance,
  updateInstance,
  deleteInstance,
  authenticateInstance,
  updateCredentials,
  removeCredentials,
  reauthenticateInstance,
  getMyMcpServers,
  discoverInstanceTools,
  getAgentMcpServers,
  authenticateAgentInstance,
  updateAgentCredentials,
  removeAgentCredentials,
} from '../../../../src/modules/mcp_servers/controller/mcp_servers_controller'
import { UnauthorizedError } from '../../../../src/libs/errors/http.errors'

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
    end: sinon.stub(),
    send: sinon.stub(),
    setHeader: sinon.stub(),
    getHeader: sinon.stub(),
    headersSent: false,
  }
  res.status.returns(res)
  res.json.returns(res)
  res.end.returns(res)
  res.send.returns(res)
  return res
}

function createMockNext(): sinon.SinonStub {
  return sinon.stub()
}

function createMockAppConfig(): any {
  return {
    connectorBackend: 'http://localhost:8088',
  }
}

describe('MCP Servers Controller', () => {
  let executeStub: sinon.SinonStub
  let handleResponseStub: sinon.SinonStub
  let handleErrorStub: sinon.SinonStub

  beforeEach(() => {
    executeStub = sinon.stub(connectorUtils, 'executeConnectorCommand')
    handleResponseStub = sinon.stub(connectorUtils, 'handleConnectorResponse')
    handleErrorStub = sinon.stub(connectorUtils, 'handleBackendError').callsFake((err: any) => err)
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('getCatalog', () => {
    it('should return a handler function', () => {
      const handler = getCatalog(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next with error when userId is missing', async () => {
      const handler = getCatalog(createMockAppConfig())
      const req = createMockRequest({ user: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError)
    })

    it('should execute connector command and handle response', async () => {
      executeStub.resolves({ statusCode: 200, data: [] })
      const handler = getCatalog(createMockAppConfig())
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(executeStub.calledOnce).to.be.true
      expect(handleResponseStub.calledOnce).to.be.true
      expect(executeStub.firstCall.args[0]).to.include(
        'http://localhost:8088/api/v1/mcp-servers/catalog',
      )
      expect(executeStub.firstCall.args[1]).to.equal('GET')
    })

    it('should pass query params to connector command URL', async () => {
      executeStub.resolves({ statusCode: 200, data: [] })
      const handler = getCatalog(createMockAppConfig())
      const req = createMockRequest({ query: { page: '2', limit: '20', search: 'slack' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      const url = executeStub.firstCall.args[0]
      expect(url).to.include('page=2')
      expect(url).to.include('limit=20')
      expect(url).to.include('search=slack')
    })
  })

  describe('getCatalogItem', () => {
    it('should return a handler function', () => {
      const handler = getCatalogItem(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next with error when userId is missing', async () => {
      const handler = getCatalogItem(createMockAppConfig())
      const req = createMockRequest({ user: {}, params: { typeId: 't1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError)
    })

    it('should execute connector command and handle response', async () => {
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = getCatalogItem(createMockAppConfig())
      const req = createMockRequest({ params: { typeId: 'github' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(executeStub.calledOnce).to.be.true
      expect(handleResponseStub.calledOnce).to.be.true
      expect(executeStub.firstCall.args[0]).to.include('/api/v1/mcp-servers/catalog/github')
      expect(executeStub.firstCall.args[1]).to.equal('GET')
    })
  })

  describe('getInstances', () => {
    it('should return a handler function', () => {
      const handler = getInstances(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next with error when userId is missing', async () => {
      const handler = getInstances(createMockAppConfig())
      const req = createMockRequest({ user: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError)
    })

    it('should execute connector command and handle response', async () => {
      executeStub.resolves({ statusCode: 200, data: [] })
      const handler = getInstances(createMockAppConfig())
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(executeStub.calledOnce).to.be.true
      expect(handleResponseStub.calledOnce).to.be.true
      expect(executeStub.firstCall.args[0]).to.include('/api/v1/mcp-servers/instances')
      expect(executeStub.firstCall.args[1]).to.equal('GET')
    })
  })

  describe('createInstance', () => {
    it('should return a handler function', () => {
      const handler = createInstance(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next with error when userId is missing', async () => {
      const handler = createInstance(createMockAppConfig())
      const req = createMockRequest({ user: {}, body: { name: 'x' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError)
    })

    it('should execute connector command and handle response', async () => {
      executeStub.resolves({ statusCode: 201, data: { id: 'i1' } })
      const handler = createInstance(createMockAppConfig())
      const body = { name: 'My MCP', typeId: 't1' }
      const req = createMockRequest({ body })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(executeStub.calledOnce).to.be.true
      expect(handleResponseStub.calledOnce).to.be.true
      expect(executeStub.firstCall.args[0]).to.include('/api/v1/mcp-servers/instances')
      expect(executeStub.firstCall.args[1]).to.equal('POST')
      expect(executeStub.firstCall.args[3]).to.deep.equal(body)
    })
  })

  describe('getInstance', () => {
    it('should return a handler function', () => {
      const handler = getInstance(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next with error when userId is missing', async () => {
      const handler = getInstance(createMockAppConfig())
      const req = createMockRequest({ user: {}, params: { instanceId: 'uuid' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError)
    })

    it('should execute connector command and handle response', async () => {
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = getInstance(createMockAppConfig())
      const req = createMockRequest({ params: { instanceId: 'inst-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(executeStub.calledOnce).to.be.true
      expect(handleResponseStub.calledOnce).to.be.true
      expect(executeStub.firstCall.args[0]).to.include('/api/v1/mcp-servers/instances/inst-1')
      expect(executeStub.firstCall.args[1]).to.equal('GET')
    })
  })

  describe('updateInstance', () => {
    it('should return a handler function', () => {
      const handler = updateInstance(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next with error when userId is missing', async () => {
      const handler = updateInstance(createMockAppConfig())
      const req = createMockRequest({ user: {}, params: { instanceId: 'i' }, body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError)
    })

    it('should execute connector command and handle response', async () => {
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = updateInstance(createMockAppConfig())
      const body = { name: 'updated' }
      const req = createMockRequest({ params: { instanceId: 'inst-1' }, body })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(executeStub.calledOnce).to.be.true
      expect(handleResponseStub.calledOnce).to.be.true
      expect(executeStub.firstCall.args[0]).to.include('/api/v1/mcp-servers/instances/inst-1')
      expect(executeStub.firstCall.args[1]).to.equal('PUT')
      expect(executeStub.firstCall.args[3]).to.deep.equal(body)
    })
  })

  describe('deleteInstance', () => {
    it('should return a handler function', () => {
      const handler = deleteInstance(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next with error when userId is missing', async () => {
      const handler = deleteInstance(createMockAppConfig())
      const req = createMockRequest({ user: {}, params: { instanceId: 'i' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError)
    })

    it('should execute connector command and handle response', async () => {
      executeStub.resolves({ statusCode: 204, data: null })
      const handler = deleteInstance(createMockAppConfig())
      const req = createMockRequest({ params: { instanceId: 'inst-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(executeStub.calledOnce).to.be.true
      expect(handleResponseStub.calledOnce).to.be.true
      expect(executeStub.firstCall.args[0]).to.include('/api/v1/mcp-servers/instances/inst-1')
      expect(executeStub.firstCall.args[1]).to.equal('DELETE')
    })
  })

  describe('authenticateInstance', () => {
    it('should return a handler function', () => {
      const handler = authenticateInstance(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next with error when userId is missing', async () => {
      const handler = authenticateInstance(createMockAppConfig())
      const req = createMockRequest({ user: {}, params: { instanceId: 'i' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError)
    })

    it('should execute connector command and handle response', async () => {
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = authenticateInstance(createMockAppConfig())
      const body = { token: 'tok' }
      const req = createMockRequest({ params: { instanceId: 'inst-1' }, body })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(executeStub.calledOnce).to.be.true
      expect(handleResponseStub.calledOnce).to.be.true
      expect(executeStub.firstCall.args[0]).to.include(
        '/api/v1/mcp-servers/instances/inst-1/authenticate',
      )
      expect(executeStub.firstCall.args[1]).to.equal('POST')
      expect(executeStub.firstCall.args[3]).to.deep.equal(body)
    })
  })

  describe('updateCredentials', () => {
    it('should return a handler function', () => {
      const handler = updateCredentials(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next with error when userId is missing', async () => {
      const handler = updateCredentials(createMockAppConfig())
      const req = createMockRequest({ user: {}, params: { instanceId: 'i' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError)
    })

    it('should execute connector command and handle response', async () => {
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = updateCredentials(createMockAppConfig())
      const body = { secret: 's' }
      const req = createMockRequest({ params: { instanceId: 'inst-1' }, body })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(executeStub.calledOnce).to.be.true
      expect(handleResponseStub.calledOnce).to.be.true
      expect(executeStub.firstCall.args[0]).to.include(
        '/api/v1/mcp-servers/instances/inst-1/credentials',
      )
      expect(executeStub.firstCall.args[1]).to.equal('PUT')
      expect(executeStub.firstCall.args[3]).to.deep.equal(body)
    })
  })

  describe('removeCredentials', () => {
    it('should return a handler function', () => {
      const handler = removeCredentials(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next with error when userId is missing', async () => {
      const handler = removeCredentials(createMockAppConfig())
      const req = createMockRequest({ user: {}, params: { instanceId: 'i' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError)
    })

    it('should execute connector command and handle response', async () => {
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = removeCredentials(createMockAppConfig())
      const req = createMockRequest({ params: { instanceId: 'inst-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(executeStub.calledOnce).to.be.true
      expect(handleResponseStub.calledOnce).to.be.true
      expect(executeStub.firstCall.args[0]).to.include(
        '/api/v1/mcp-servers/instances/inst-1/credentials',
      )
      expect(executeStub.firstCall.args[1]).to.equal('DELETE')
    })
  })

  describe('reauthenticateInstance', () => {
    it('should return a handler function', () => {
      const handler = reauthenticateInstance(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next with error when userId is missing', async () => {
      const handler = reauthenticateInstance(createMockAppConfig())
      const req = createMockRequest({ user: {}, params: { instanceId: 'i' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError)
    })

    it('should execute connector command and handle response', async () => {
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = reauthenticateInstance(createMockAppConfig())
      const req = createMockRequest({ params: { instanceId: 'inst-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(executeStub.calledOnce).to.be.true
      expect(handleResponseStub.calledOnce).to.be.true
      expect(executeStub.firstCall.args[0]).to.include(
        '/api/v1/mcp-servers/instances/inst-1/reauthenticate',
      )
      expect(executeStub.firstCall.args[1]).to.equal('POST')
    })
  })

  describe('getMyMcpServers', () => {
    it('should return a handler function', () => {
      const handler = getMyMcpServers(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next with error when userId is missing', async () => {
      const handler = getMyMcpServers(createMockAppConfig())
      const req = createMockRequest({ user: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError)
    })

    it('should execute connector command and handle response', async () => {
      executeStub.resolves({ statusCode: 200, data: [] })
      const handler = getMyMcpServers(createMockAppConfig())
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(executeStub.calledOnce).to.be.true
      expect(handleResponseStub.calledOnce).to.be.true
      expect(executeStub.firstCall.args[0]).to.include('/api/v1/mcp-servers/my-mcp-servers')
      expect(executeStub.firstCall.args[1]).to.equal('GET')
    })

    it('should pass query params to connector command URL', async () => {
      executeStub.resolves({ statusCode: 200, data: [] })
      const handler = getMyMcpServers(createMockAppConfig())
      const req = createMockRequest({
        query: {
          page: '1',
          limit: '10',
          search: 'x',
          includeRegistry: 'true',
          authStatus: 'authenticated',
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      const url = executeStub.firstCall.args[0]
      expect(url).to.include('page=1')
      expect(url).to.include('limit=10')
      expect(url).to.include('search=x')
      expect(url).to.include('includeRegistry=true')
      expect(url).to.include('authStatus=authenticated')
    })
  })

  describe('discoverInstanceTools', () => {
    it('should return a handler function', () => {
      const handler = discoverInstanceTools(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next with error when userId is missing', async () => {
      const handler = discoverInstanceTools(createMockAppConfig())
      const req = createMockRequest({ user: {}, params: { instanceId: 'i' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError)
    })

    it('should execute connector command and handle response', async () => {
      executeStub.resolves({ statusCode: 200, data: { tools: [] } })
      const handler = discoverInstanceTools(createMockAppConfig())
      const req = createMockRequest({ params: { instanceId: 'inst-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(executeStub.calledOnce).to.be.true
      expect(handleResponseStub.calledOnce).to.be.true
      expect(executeStub.firstCall.args[0]).to.include('/api/v1/mcp-servers/instances/inst-1/tools')
      expect(executeStub.firstCall.args[1]).to.equal('GET')
    })
  })

  describe('getAgentMcpServers', () => {
    it('should return a handler function', () => {
      const handler = getAgentMcpServers(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next with error when userId is missing', async () => {
      const handler = getAgentMcpServers(createMockAppConfig())
      const req = createMockRequest({ user: {}, params: { agentKey: 'a' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError)
    })

    it('should execute connector command and handle response', async () => {
      executeStub.resolves({ statusCode: 200, data: [] })
      const handler = getAgentMcpServers(createMockAppConfig())
      const req = createMockRequest({ params: { agentKey: 'agent-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(executeStub.calledOnce).to.be.true
      expect(handleResponseStub.calledOnce).to.be.true
      expect(executeStub.firstCall.args[0]).to.include('/api/v1/mcp-servers/agents/agent-1')
      expect(executeStub.firstCall.args[1]).to.equal('GET')
    })

    it('should pass query params to connector command URL', async () => {
      executeStub.resolves({ statusCode: 200, data: [] })
      const handler = getAgentMcpServers(createMockAppConfig())
      const req = createMockRequest({
        params: { agentKey: 'agent-1' },
        query: { page: '2', limit: '5', search: 'mcp', includeRegistry: 'false' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      const url = executeStub.firstCall.args[0]
      expect(url).to.include('page=2')
      expect(url).to.include('limit=5')
      expect(url).to.include('search=mcp')
      expect(url).to.include('includeRegistry=false')
    })
  })

  describe('authenticateAgentInstance', () => {
    it('should return a handler function', () => {
      const handler = authenticateAgentInstance(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next with error when userId is missing', async () => {
      const handler = authenticateAgentInstance(createMockAppConfig())
      const req = createMockRequest({
        user: {},
        params: { agentKey: 'a', instanceId: 'i' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError)
    })

    it('should execute connector command and handle response', async () => {
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = authenticateAgentInstance(createMockAppConfig())
      const body = { creds: {} }
      const req = createMockRequest({
        params: { agentKey: 'agent-1', instanceId: 'inst-1' },
        body,
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(executeStub.calledOnce).to.be.true
      expect(handleResponseStub.calledOnce).to.be.true
      expect(executeStub.firstCall.args[0]).to.include(
        '/api/v1/mcp-servers/agents/agent-1/instances/inst-1/authenticate',
      )
      expect(executeStub.firstCall.args[1]).to.equal('POST')
      expect(executeStub.firstCall.args[3]).to.deep.equal(body)
    })
  })

  describe('updateAgentCredentials', () => {
    it('should return a handler function', () => {
      const handler = updateAgentCredentials(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next with error when userId is missing', async () => {
      const handler = updateAgentCredentials(createMockAppConfig())
      const req = createMockRequest({
        user: {},
        params: { agentKey: 'a', instanceId: 'i' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError)
    })

    it('should execute connector command and handle response', async () => {
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = updateAgentCredentials(createMockAppConfig())
      const body = { token: 't' }
      const req = createMockRequest({
        params: { agentKey: 'agent-1', instanceId: 'inst-1' },
        body,
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(executeStub.calledOnce).to.be.true
      expect(handleResponseStub.calledOnce).to.be.true
      expect(executeStub.firstCall.args[0]).to.include(
        '/api/v1/mcp-servers/agents/agent-1/instances/inst-1/credentials',
      )
      expect(executeStub.firstCall.args[1]).to.equal('PUT')
      expect(executeStub.firstCall.args[3]).to.deep.equal(body)
    })
  })

  describe('removeAgentCredentials', () => {
    it('should return a handler function', () => {
      const handler = removeAgentCredentials(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next with error when userId is missing', async () => {
      const handler = removeAgentCredentials(createMockAppConfig())
      const req = createMockRequest({
        user: {},
        params: { agentKey: 'a', instanceId: 'i' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError)
    })

    it('should execute connector command and handle response', async () => {
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = removeAgentCredentials(createMockAppConfig())
      const req = createMockRequest({
        params: { agentKey: 'agent-1', instanceId: 'inst-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(executeStub.calledOnce).to.be.true
      expect(handleResponseStub.calledOnce).to.be.true
      expect(executeStub.firstCall.args[0]).to.include(
        '/api/v1/mcp-servers/agents/agent-1/instances/inst-1/credentials',
      )
      expect(executeStub.firstCall.args[1]).to.equal('DELETE')
    })
  })
})
