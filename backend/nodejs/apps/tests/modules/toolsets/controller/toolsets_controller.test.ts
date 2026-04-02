import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as connectorUtils from '../../../../src/modules/tokens_manager/utils/connector.utils'
import {
  getRegistryToolsets,
  getConfiguredToolsets,
  getToolsetSchema,
  createToolset,
  checkToolsetStatus,
  getToolsetConfig,
  saveToolsetConfig,
  updateToolsetConfig,
  deleteToolsetConfig,
  reauthenticateToolset,
  getOAuthAuthorizationUrl,
  handleOAuthCallback,
  getToolsetInstances,
  createToolsetInstance,
  getToolsetInstance,
  updateToolsetInstance,
  deleteToolsetInstance,
  getMyToolsets,
  authenticateToolsetInstance,
  updateUserToolsetInstance,
  removeToolsetCredentials,
  reauthenticateToolsetInstance,
} from '../../../../src/modules/toolsets/controller/toolsets_controller'
import { BadRequestError, UnauthorizedError } from '../../../../src/libs/errors/http.errors'

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

describe('Toolsets Controller', () => {
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

  // -----------------------------------------------------------------------
  // getRegistryToolsets
  // -----------------------------------------------------------------------
  describe('getRegistryToolsets', () => {
    it('should return a handler function', () => {
      const handler = getRegistryToolsets(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next with UnauthorizedError when userId is missing', async () => {
      const handler = getRegistryToolsets(createMockAppConfig())
      const req = createMockRequest({ user: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should execute connector command and handle response', async () => {
      executeStub.resolves({ statusCode: 200, data: [{ name: 'toolset-1' }] })
      const handler = getRegistryToolsets(createMockAppConfig())
      const req = createMockRequest({ query: { page: '1', limit: '10', search: 'test' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(executeStub.calledOnce).to.be.true
      expect(handleResponseStub.calledOnce).to.be.true
    })

    it('should pass query params to connector command URL', async () => {
      executeStub.resolves({ statusCode: 200, data: [] })
      const handler = getRegistryToolsets(createMockAppConfig())
      const req = createMockRequest({ query: { page: '2', limit: '20' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      const url = executeStub.firstCall.args[0]
      expect(url).to.include('page=2')
      expect(url).to.include('limit=20')
    })
  })

  // -----------------------------------------------------------------------
  // getConfiguredToolsets
  // -----------------------------------------------------------------------
  describe('getConfiguredToolsets', () => {
    it('should return a handler function', () => {
      const handler = getConfiguredToolsets(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next with error when userId is missing', async () => {
      const handler = getConfiguredToolsets(createMockAppConfig())
      const req = createMockRequest({ user: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call connector command for configured toolsets', async () => {
      executeStub.resolves({ statusCode: 200, data: [] })
      const handler = getConfiguredToolsets(createMockAppConfig())
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(executeStub.calledOnce).to.be.true
      expect(executeStub.firstCall.args[0]).to.include('/toolsets/configured')
    })
  })

  // -----------------------------------------------------------------------
  // getToolsetSchema
  // -----------------------------------------------------------------------
  describe('getToolsetSchema', () => {
    it('should return a handler function', () => {
      const handler = getToolsetSchema(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next with BadRequestError when toolsetType is missing', async () => {
      const handler = getToolsetSchema(createMockAppConfig())
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call connector command with toolsetType', async () => {
      executeStub.resolves({ statusCode: 200, data: { schema: {} } })
      const handler = getToolsetSchema(createMockAppConfig())
      const req = createMockRequest({ params: { toolsetType: 'jira' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(executeStub.firstCall.args[0]).to.include('/toolsets/registry/jira/schema')
    })
  })

  // -----------------------------------------------------------------------
  // createToolset
  // -----------------------------------------------------------------------
  describe('createToolset', () => {
    it('should return a handler function', () => {
      const handler = createToolset(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next with BadRequestError when name is missing', async () => {
      const handler = createToolset(createMockAppConfig())
      const req = createMockRequest({ body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call connector command with body data', async () => {
      executeStub.resolves({ statusCode: 200, data: { id: 'toolset-1' } })
      const handler = createToolset(createMockAppConfig())
      const body = { name: 'My Toolset', type: 'jira' }
      const req = createMockRequest({ body })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(executeStub.calledOnce).to.be.true
      expect(executeStub.firstCall.args[3]).to.deep.equal(body)
    })
  })

  // -----------------------------------------------------------------------
  // checkToolsetStatus
  // -----------------------------------------------------------------------
  describe('checkToolsetStatus', () => {
    it('should return a handler function', () => {
      const handler = checkToolsetStatus(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next with BadRequestError when toolsetId is missing', async () => {
      const handler = checkToolsetStatus(createMockAppConfig())
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call connector command with toolsetId', async () => {
      executeStub.resolves({ statusCode: 200, data: { status: 'active' } })
      const handler = checkToolsetStatus(createMockAppConfig())
      const req = createMockRequest({ params: { toolsetId: 'ts-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(executeStub.firstCall.args[0]).to.include('/toolsets/ts-1/status')
    })
  })

  // -----------------------------------------------------------------------
  // getToolsetConfig
  // -----------------------------------------------------------------------
  describe('getToolsetConfig', () => {
    it('should call next with BadRequestError when toolsetId is missing', async () => {
      const handler = getToolsetConfig(createMockAppConfig())
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should call connector command with correct path', async () => {
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = getToolsetConfig(createMockAppConfig())
      const req = createMockRequest({ params: { toolsetId: 'ts-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(executeStub.firstCall.args[0]).to.include('/toolsets/ts-1/config')
    })
  })

  // -----------------------------------------------------------------------
  // saveToolsetConfig
  // -----------------------------------------------------------------------
  describe('saveToolsetConfig', () => {
    it('should call next with BadRequestError when toolsetId is missing', async () => {
      const handler = saveToolsetConfig(createMockAppConfig())
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should POST config data to connector', async () => {
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = saveToolsetConfig(createMockAppConfig())
      const configData = { apiKey: 'key', url: 'http://jira.example.com' }
      const req = createMockRequest({ params: { toolsetId: 'ts-1' }, body: configData })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(executeStub.firstCall.args[3]).to.deep.equal(configData)
    })
  })

  // -----------------------------------------------------------------------
  // updateToolsetConfig
  // -----------------------------------------------------------------------
  describe('updateToolsetConfig', () => {
    it('should call next with BadRequestError when toolsetId is missing', async () => {
      const handler = updateToolsetConfig(createMockAppConfig())
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should use PUT method for update', async () => {
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = updateToolsetConfig(createMockAppConfig())
      const req = createMockRequest({ params: { toolsetId: 'ts-1' }, body: { key: 'val' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(executeStub.firstCall.args[1]).to.equal('PUT')
    })
  })

  // -----------------------------------------------------------------------
  // deleteToolsetConfig
  // -----------------------------------------------------------------------
  describe('deleteToolsetConfig', () => {
    it('should call next with BadRequestError when toolsetId is missing', async () => {
      const handler = deleteToolsetConfig(createMockAppConfig())
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should use DELETE method', async () => {
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = deleteToolsetConfig(createMockAppConfig())
      const req = createMockRequest({ params: { toolsetId: 'ts-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(executeStub.firstCall.args[1]).to.equal('DELETE')
    })
  })

  // -----------------------------------------------------------------------
  // reauthenticateToolset
  // -----------------------------------------------------------------------
  describe('reauthenticateToolset', () => {
    it('should call next with BadRequestError when toolsetId is missing', async () => {
      const handler = reauthenticateToolset(createMockAppConfig())
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should POST to reauthenticate endpoint', async () => {
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = reauthenticateToolset(createMockAppConfig())
      const req = createMockRequest({ params: { toolsetId: 'ts-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(executeStub.firstCall.args[0]).to.include('/toolsets/ts-1/reauthenticate')
    })
  })

  // -----------------------------------------------------------------------
  // getOAuthAuthorizationUrl
  // -----------------------------------------------------------------------
  describe('getOAuthAuthorizationUrl', () => {
    it('should call next with BadRequestError when toolsetId is missing', async () => {
      const handler = getOAuthAuthorizationUrl(createMockAppConfig())
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should pass base_url query param', async () => {
      executeStub.resolves({ statusCode: 200, data: { authUrl: 'https://auth.example.com' } })
      const handler = getOAuthAuthorizationUrl(createMockAppConfig())
      const req = createMockRequest({
        params: { toolsetId: 'ts-1' },
        query: { base_url: 'http://localhost:3000' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(executeStub.firstCall.args[0]).to.include('base_url=')
    })
  })

  // -----------------------------------------------------------------------
  // handleOAuthCallback
  // -----------------------------------------------------------------------
  describe('handleOAuthCallback', () => {
    it('should return a handler function', () => {
      const handler = handleOAuthCallback(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should handle redirect response (302)', async () => {
      executeStub.resolves({
        statusCode: 302,
        headers: { location: 'https://example.com/callback' },
        data: null,
      })
      // Restore handleResponseStub so we can test the redirect logic
      handleResponseStub.restore()
      handleResponseStub = sinon.stub(connectorUtils, 'handleConnectorResponse')

      const handler = handleOAuthCallback(createMockAppConfig())
      const req = createMockRequest({
        query: { code: 'auth-code', state: 'state-val' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.have.property('redirectUrl', 'https://example.com/callback')
    })

    it('should handle JSON response with redirect_url', async () => {
      executeStub.resolves({
        statusCode: 200,
        data: { redirect_url: 'https://example.com/success' },
      })
      handleResponseStub.restore()
      handleResponseStub = sinon.stub(connectorUtils, 'handleConnectorResponse')

      const handler = handleOAuthCallback(createMockAppConfig())
      const req = createMockRequest({ query: { code: 'code' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.have.property('redirectUrl', 'https://example.com/success')
    })

    it('should reject invalid redirect URL', async () => {
      executeStub.resolves({
        statusCode: 302,
        headers: { location: 'javascript:alert(1)' },
      })

      const handler = handleOAuthCallback(createMockAppConfig())
      const req = createMockRequest({ query: { code: 'code' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // getToolsetInstances
  // -----------------------------------------------------------------------
  describe('getToolsetInstances', () => {
    it('should call next with UnauthorizedError when userId is missing', async () => {
      const handler = getToolsetInstances(createMockAppConfig())
      const req = createMockRequest({ user: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should pass query params to connector URL', async () => {
      executeStub.resolves({ statusCode: 200, data: [] })
      const handler = getToolsetInstances(createMockAppConfig())
      const req = createMockRequest({ query: { page: '1', limit: '5', search: 'jira' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      const url = executeStub.firstCall.args[0]
      expect(url).to.include('page=1')
      expect(url).to.include('limit=5')
      expect(url).to.include('search=jira')
    })
  })

  // -----------------------------------------------------------------------
  // createToolsetInstance
  // -----------------------------------------------------------------------
  describe('createToolsetInstance', () => {
    it('should call next with UnauthorizedError when userId is missing', async () => {
      const handler = createToolsetInstance(createMockAppConfig())
      const req = createMockRequest({ user: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should call next when instanceName is missing', async () => {
      const handler = createToolsetInstance(createMockAppConfig())
      const req = createMockRequest({ body: { toolsetType: 'jira', authType: 'api_key' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should call next when toolsetType is missing', async () => {
      const handler = createToolsetInstance(createMockAppConfig())
      const req = createMockRequest({ body: { instanceName: 'My JIRA', authType: 'api_key' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should call next when authType is missing', async () => {
      const handler = createToolsetInstance(createMockAppConfig())
      const req = createMockRequest({ body: { instanceName: 'My JIRA', toolsetType: 'jira' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should POST instance data to connector', async () => {
      executeStub.resolves({ statusCode: 200, data: { id: 'inst-1' } })
      const body = { instanceName: 'My JIRA', toolsetType: 'jira', authType: 'api_key' }
      const handler = createToolsetInstance(createMockAppConfig())
      const req = createMockRequest({ body })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(executeStub.calledOnce).to.be.true
      expect(executeStub.firstCall.args[0]).to.include('/toolsets/instances')
    })
  })

  // -----------------------------------------------------------------------
  // getToolsetInstance
  // -----------------------------------------------------------------------
  describe('getToolsetInstance', () => {
    it('should call next with BadRequestError when instanceId is missing', async () => {
      const handler = getToolsetInstance(createMockAppConfig())
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // updateToolsetInstance
  // -----------------------------------------------------------------------
  describe('updateToolsetInstance', () => {
    it('should call next with BadRequestError when instanceId is missing', async () => {
      const handler = updateToolsetInstance(createMockAppConfig())
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // deleteToolsetInstance
  // -----------------------------------------------------------------------
  describe('deleteToolsetInstance', () => {
    it('should call next with BadRequestError when instanceId is missing', async () => {
      const handler = deleteToolsetInstance(createMockAppConfig())
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // getMyToolsets
  // -----------------------------------------------------------------------
  describe('getMyToolsets', () => {
    it('should call next with UnauthorizedError when userId is missing', async () => {
      const handler = getMyToolsets(createMockAppConfig())
      const req = createMockRequest({ user: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should pass search query to URL', async () => {
      executeStub.resolves({ statusCode: 200, data: [] })
      const handler = getMyToolsets(createMockAppConfig())
      const req = createMockRequest({ query: { search: 'jira' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(executeStub.firstCall.args[0]).to.include('search=jira')
    })
  })

  // -----------------------------------------------------------------------
  // authenticateToolsetInstance
  // -----------------------------------------------------------------------
  describe('authenticateToolsetInstance', () => {
    it('should call next with BadRequestError when instanceId is missing', async () => {
      const handler = authenticateToolsetInstance(createMockAppConfig())
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // updateUserToolsetInstance
  // -----------------------------------------------------------------------
  describe('updateUserToolsetInstance', () => {
    it('should call next with BadRequestError when instanceId is missing', async () => {
      const handler = updateUserToolsetInstance(createMockAppConfig())
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // removeToolsetCredentials
  // -----------------------------------------------------------------------
  describe('removeToolsetCredentials', () => {
    it('should call next with BadRequestError when instanceId is missing', async () => {
      const handler = removeToolsetCredentials(createMockAppConfig())
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // reauthenticateToolsetInstance
  // -----------------------------------------------------------------------
  describe('reauthenticateToolsetInstance', () => {
    it('should call next with BadRequestError when instanceId is missing', async () => {
      const handler = reauthenticateToolsetInstance(createMockAppConfig())
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should call connector command with correct URL', async () => {
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = reauthenticateToolsetInstance(createMockAppConfig())
      const req = createMockRequest({ params: { instanceId: 'inst-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(executeStub.firstCall.args[0]).to.include('/toolsets/instances/inst-1/reauthenticate')
      expect(executeStub.firstCall.args[1]).to.equal('POST')
    })
  })

  // -----------------------------------------------------------------------
  // getToolsetInstance (happy path)
  // -----------------------------------------------------------------------
  describe('getToolsetInstance (extended)', () => {
    it('should call connector command with correct URL', async () => {
      executeStub.resolves({ statusCode: 200, data: { id: 'inst-1' } })
      const handler = getToolsetInstance(createMockAppConfig())
      const req = createMockRequest({ params: { instanceId: 'inst-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(executeStub.firstCall.args[0]).to.include('/toolsets/instances/inst-1')
      expect(handleResponseStub.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // updateToolsetInstance (happy path)
  // -----------------------------------------------------------------------
  describe('updateToolsetInstance (extended)', () => {
    it('should PUT to correct endpoint', async () => {
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = updateToolsetInstance(createMockAppConfig())
      const req = createMockRequest({ params: { instanceId: 'inst-1' }, body: { name: 'updated' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(executeStub.firstCall.args[0]).to.include('/toolsets/instances/inst-1')
      expect(executeStub.firstCall.args[1]).to.equal('PUT')
      expect(executeStub.firstCall.args[3]).to.deep.equal({ name: 'updated' })
    })
  })

  // -----------------------------------------------------------------------
  // deleteToolsetInstance (happy path)
  // -----------------------------------------------------------------------
  describe('deleteToolsetInstance (extended)', () => {
    it('should DELETE to correct endpoint', async () => {
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = deleteToolsetInstance(createMockAppConfig())
      const req = createMockRequest({ params: { instanceId: 'inst-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(executeStub.firstCall.args[0]).to.include('/toolsets/instances/inst-1')
      expect(executeStub.firstCall.args[1]).to.equal('DELETE')
    })
  })

  // -----------------------------------------------------------------------
  // authenticateToolsetInstance (happy path)
  // -----------------------------------------------------------------------
  describe('authenticateToolsetInstance (extended)', () => {
    it('should POST credentials to correct endpoint', async () => {
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = authenticateToolsetInstance(createMockAppConfig())
      const creds = { apiKey: 'secret123' }
      const req = createMockRequest({ params: { instanceId: 'inst-1' }, body: creds })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(executeStub.firstCall.args[0]).to.include('/toolsets/instances/inst-1/authenticate')
      expect(executeStub.firstCall.args[1]).to.equal('POST')
      expect(executeStub.firstCall.args[3]).to.deep.equal(creds)
    })
  })

  // -----------------------------------------------------------------------
  // updateUserToolsetInstance (happy path)
  // -----------------------------------------------------------------------
  describe('updateUserToolsetInstance (extended)', () => {
    it('should PUT credentials to correct endpoint', async () => {
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = updateUserToolsetInstance(createMockAppConfig())
      const body = { token: 'newtoken' }
      const req = createMockRequest({ params: { instanceId: 'inst-1' }, body })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(executeStub.firstCall.args[0]).to.include('/toolsets/instances/inst-1/credentials')
      expect(executeStub.firstCall.args[1]).to.equal('PUT')
    })
  })

  // -----------------------------------------------------------------------
  // removeToolsetCredentials (happy path)
  // -----------------------------------------------------------------------
  describe('removeToolsetCredentials (extended)', () => {
    it('should DELETE credentials from correct endpoint', async () => {
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = removeToolsetCredentials(createMockAppConfig())
      const req = createMockRequest({ params: { instanceId: 'inst-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(executeStub.firstCall.args[0]).to.include('/toolsets/instances/inst-1/credentials')
      expect(executeStub.firstCall.args[1]).to.equal('DELETE')
    })
  })

  // -----------------------------------------------------------------------
  // getInstanceOAuthAuthorizationUrl
  // -----------------------------------------------------------------------
  describe('getInstanceOAuthAuthorizationUrl', () => {
    it('should call next with BadRequestError when instanceId is missing', async () => {
      const { getInstanceOAuthAuthorizationUrl } = require('../../../../src/modules/toolsets/controller/toolsets_controller')
      const handler = getInstanceOAuthAuthorizationUrl(createMockAppConfig())
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should pass base_url query param', async () => {
      const { getInstanceOAuthAuthorizationUrl } = require('../../../../src/modules/toolsets/controller/toolsets_controller')
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = getInstanceOAuthAuthorizationUrl(createMockAppConfig())
      const req = createMockRequest({
        params: { instanceId: 'inst-1' },
        query: { base_url: 'http://localhost:3000' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(executeStub.firstCall.args[0]).to.include('base_url=')
      expect(executeStub.firstCall.args[0]).to.include('/toolsets/instances/inst-1/oauth/authorize')
    })
  })

  // -----------------------------------------------------------------------
  // getInstanceStatus
  // -----------------------------------------------------------------------
  describe('getInstanceStatus', () => {
    it('should call next with BadRequestError when instanceId is missing', async () => {
      const { getInstanceStatus } = require('../../../../src/modules/toolsets/controller/toolsets_controller')
      const handler = getInstanceStatus(createMockAppConfig())
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should call correct endpoint', async () => {
      const { getInstanceStatus } = require('../../../../src/modules/toolsets/controller/toolsets_controller')
      executeStub.resolves({ statusCode: 200, data: { authenticated: true } })
      const handler = getInstanceStatus(createMockAppConfig())
      const req = createMockRequest({ params: { instanceId: 'inst-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(executeStub.firstCall.args[0]).to.include('/toolsets/instances/inst-1/status')
    })
  })

  // -----------------------------------------------------------------------
  // listToolsetOAuthConfigs
  // -----------------------------------------------------------------------
  describe('listToolsetOAuthConfigs', () => {
    it('should call next with BadRequestError when toolsetType is missing', async () => {
      const { listToolsetOAuthConfigs } = require('../../../../src/modules/toolsets/controller/toolsets_controller')
      const handler = listToolsetOAuthConfigs(createMockAppConfig())
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should call correct endpoint for toolset type', async () => {
      const { listToolsetOAuthConfigs } = require('../../../../src/modules/toolsets/controller/toolsets_controller')
      executeStub.resolves({ statusCode: 200, data: [] })
      const handler = listToolsetOAuthConfigs(createMockAppConfig())
      const req = createMockRequest({ params: { toolsetType: 'jira' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(executeStub.firstCall.args[0]).to.include('/toolsets/oauth-configs/jira')
    })
  })

  // -----------------------------------------------------------------------
  // updateToolsetOAuthConfig
  // -----------------------------------------------------------------------
  describe('updateToolsetOAuthConfig', () => {
    it('should call next when toolsetType is missing', async () => {
      const { updateToolsetOAuthConfig } = require('../../../../src/modules/toolsets/controller/toolsets_controller')
      const handler = updateToolsetOAuthConfig(createMockAppConfig())
      const req = createMockRequest({ params: { oauthConfigId: 'oc-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should call next when oauthConfigId is missing', async () => {
      const { updateToolsetOAuthConfig } = require('../../../../src/modules/toolsets/controller/toolsets_controller')
      const handler = updateToolsetOAuthConfig(createMockAppConfig())
      const req = createMockRequest({ params: { toolsetType: 'jira' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should PUT config to correct endpoint', async () => {
      const { updateToolsetOAuthConfig } = require('../../../../src/modules/toolsets/controller/toolsets_controller')
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = updateToolsetOAuthConfig(createMockAppConfig())
      const req = createMockRequest({
        params: { toolsetType: 'jira', oauthConfigId: 'oc-1' },
        body: { clientId: 'new-id' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(executeStub.firstCall.args[0]).to.include('/toolsets/oauth-configs/jira/oc-1')
      expect(executeStub.firstCall.args[1]).to.equal('PUT')
    })
  })

  // -----------------------------------------------------------------------
  // deleteToolsetOAuthConfig
  // -----------------------------------------------------------------------
  describe('deleteToolsetOAuthConfig', () => {
    it('should call next when toolsetType is missing', async () => {
      const { deleteToolsetOAuthConfig } = require('../../../../src/modules/toolsets/controller/toolsets_controller')
      const handler = deleteToolsetOAuthConfig(createMockAppConfig())
      const req = createMockRequest({ params: { oauthConfigId: 'oc-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should call next when oauthConfigId is missing', async () => {
      const { deleteToolsetOAuthConfig } = require('../../../../src/modules/toolsets/controller/toolsets_controller')
      const handler = deleteToolsetOAuthConfig(createMockAppConfig())
      const req = createMockRequest({ params: { toolsetType: 'jira' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should DELETE from correct endpoint', async () => {
      const { deleteToolsetOAuthConfig } = require('../../../../src/modules/toolsets/controller/toolsets_controller')
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = deleteToolsetOAuthConfig(createMockAppConfig())
      const req = createMockRequest({ params: { toolsetType: 'jira', oauthConfigId: 'oc-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(executeStub.firstCall.args[0]).to.include('/toolsets/oauth-configs/jira/oc-1')
      expect(executeStub.firstCall.args[1]).to.equal('DELETE')
    })
  })

  // -----------------------------------------------------------------------
  // handleOAuthCallback – additional edge cases
  // -----------------------------------------------------------------------
  describe('handleOAuthCallback (extended)', () => {
    it('should handle 307 redirect', async () => {
      executeStub.resolves({
        statusCode: 307,
        headers: { location: 'https://example.com/redirect' },
        data: null,
      })
      handleResponseStub.restore()
      handleResponseStub = sinon.stub(connectorUtils, 'handleConnectorResponse')

      const handler = handleOAuthCallback(createMockAppConfig())
      const req = createMockRequest({ query: { code: 'code', state: 'state' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.have.property('redirectUrl', 'https://example.com/redirect')
    })

    it('should reject invalid redirect_url in JSON response', async () => {
      executeStub.resolves({
        statusCode: 200,
        data: { redirect_url: 'javascript:alert(1)' },
      })
      handleResponseStub.restore()
      handleResponseStub = sinon.stub(connectorUtils, 'handleConnectorResponse')

      const handler = handleOAuthCallback(createMockAppConfig())
      const req = createMockRequest({ query: { code: 'code' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should pass all query params to backend', async () => {
      executeStub.resolves({ statusCode: 200, data: {} })
      const handler = handleOAuthCallback(createMockAppConfig())
      const req = createMockRequest({
        query: { code: 'c', state: 's', error: 'e', base_url: 'http://localhost' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      const url = executeStub.firstCall.args[0]
      expect(url).to.include('code=c')
      expect(url).to.include('state=s')
      expect(url).to.include('error=e')
      expect(url).to.include('base_url=')
    })

    it('should fall through to handleConnectorResponse for normal responses', async () => {
      executeStub.resolves({ statusCode: 200, data: { result: 'ok' } })
      handleResponseStub.restore()
      handleResponseStub = sinon.stub(connectorUtils, 'handleConnectorResponse')

      const handler = handleOAuthCallback(createMockAppConfig())
      const req = createMockRequest({ query: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(handleResponseStub.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Error handling in connector command failures
  // -----------------------------------------------------------------------
  describe('error handling across controllers', () => {
    it('getRegistryToolsets should call next when executeConnectorCommand throws', async () => {
      executeStub.rejects(new Error('network error'))
      const handler = getRegistryToolsets(createMockAppConfig())
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('getConfiguredToolsets should call next when executeConnectorCommand throws', async () => {
      executeStub.rejects(new Error('network error'))
      const handler = getConfiguredToolsets(createMockAppConfig())
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('getToolsetSchema should call next when executeConnectorCommand throws', async () => {
      executeStub.rejects(new Error('network error'))
      const handler = getToolsetSchema(createMockAppConfig())
      const req = createMockRequest({ params: { toolsetType: 'jira' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('createToolset should call next when executeConnectorCommand throws', async () => {
      executeStub.rejects(new Error('network error'))
      const handler = createToolset(createMockAppConfig())
      const req = createMockRequest({ body: { name: 'My Toolset' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('checkToolsetStatus should call next when command throws', async () => {
      executeStub.rejects(new Error('network error'))
      const handler = checkToolsetStatus(createMockAppConfig())
      const req = createMockRequest({ params: { toolsetId: 'ts-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('saveToolsetConfig should call next when command throws', async () => {
      executeStub.rejects(new Error('network error'))
      const handler = saveToolsetConfig(createMockAppConfig())
      const req = createMockRequest({ params: { toolsetId: 'ts-1' }, body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('updateToolsetConfig should call next when command throws', async () => {
      executeStub.rejects(new Error('network error'))
      const handler = updateToolsetConfig(createMockAppConfig())
      const req = createMockRequest({ params: { toolsetId: 'ts-1' }, body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('deleteToolsetConfig should call next when command throws', async () => {
      executeStub.rejects(new Error('network error'))
      const handler = deleteToolsetConfig(createMockAppConfig())
      const req = createMockRequest({ params: { toolsetId: 'ts-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('reauthenticateToolset should call next when command throws', async () => {
      executeStub.rejects(new Error('network error'))
      const handler = reauthenticateToolset(createMockAppConfig())
      const req = createMockRequest({ params: { toolsetId: 'ts-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('getOAuthAuthorizationUrl should call next when command throws', async () => {
      executeStub.rejects(new Error('network error'))
      const handler = getOAuthAuthorizationUrl(createMockAppConfig())
      const req = createMockRequest({ params: { toolsetId: 'ts-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })
})
