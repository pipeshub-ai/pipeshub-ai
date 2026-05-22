import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as connectorUtils from '../../../../src/modules/tokens_manager/utils/connector.utils'
import {
  getRegistryToolsets,
  getToolsetSchema,
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
  getInstanceOAuthAuthorizationUrl,
  getInstanceStatus,
  listToolsetOAuthConfigs,
  updateToolsetOAuthConfig,
  deleteToolsetOAuthConfig,
} from '../../../../src/modules/toolsets/controller/toolsets_controller'
import { BadRequestError, UnauthorizedError } from '../../../../src/libs/errors/http.errors'

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
    headersSent: false,
  }
  res.status.returns(res)
  res.json.returns(res)
  res.end.returns(res)
  res.send.returns(res)
  return res
}

describe('ToolsetsController - additional coverage', () => {
  const appConfig = {
    connectorBackend: 'http://connector:8088',
  } as any

  let executeStub: sinon.SinonStub
  let handleErrorStub: sinon.SinonStub
  let handleResponseStub: sinon.SinonStub

  beforeEach(() => {
    executeStub = sinon.stub(connectorUtils, 'executeConnectorCommand')
    handleErrorStub = sinon.stub(connectorUtils, 'handleBackendError')
    handleResponseStub = sinon.stub(connectorUtils, 'handleConnectorResponse')
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('getInstanceOAuthAuthorizationUrl', () => {
    it('should throw BadRequestError when instanceId is missing', async () => {
      const handler = getInstanceOAuthAuthorizationUrl(appConfig)
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = sinon.stub()

      handleErrorStub.returns(new BadRequestError('instanceId is required'))

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should pass base_url query param', async () => {
      executeStub.resolves({ statusCode: 200, data: { url: 'https://oauth/authorize' } })
      const handler = getInstanceOAuthAuthorizationUrl(appConfig)
      const req = createMockRequest({
        params: { instanceId: 'inst-1' },
        query: { base_url: 'http://frontend' },
      })
      const res = createMockResponse()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(handleResponseStub.calledOnce).to.be.true
    })
  })

  describe('getInstanceStatus', () => {
    it('should throw BadRequestError when instanceId is missing', async () => {
      const handler = getInstanceStatus(appConfig)
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = sinon.stub()

      handleErrorStub.returns(new BadRequestError('instanceId is required'))

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should call connector backend for instance status', async () => {
      executeStub.resolves({ statusCode: 200, data: { authenticated: true } })
      const handler = getInstanceStatus(appConfig)
      const req = createMockRequest({ params: { instanceId: 'inst-1' } })
      const res = createMockResponse()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(handleResponseStub.calledOnce).to.be.true
    })
  })

  describe('listToolsetOAuthConfigs', () => {
    it('should throw BadRequestError when toolsetType is missing', async () => {
      const handler = listToolsetOAuthConfigs(appConfig)
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = sinon.stub()

      handleErrorStub.returns(new BadRequestError('toolsetType is required'))

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should call connector backend for OAuth configs', async () => {
      executeStub.resolves({ statusCode: 200, data: [] })
      const handler = listToolsetOAuthConfigs(appConfig)
      const req = createMockRequest({ params: { toolsetType: 'github' } })
      const res = createMockResponse()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(handleResponseStub.calledOnce).to.be.true
    })
  })

  describe('updateToolsetOAuthConfig', () => {
    it('should throw BadRequestError when toolsetType is missing', async () => {
      const handler = updateToolsetOAuthConfig(appConfig)
      const req = createMockRequest({ params: { oauthConfigId: 'oc-1' } })
      const res = createMockResponse()
      const next = sinon.stub()

      handleErrorStub.returns(new BadRequestError('toolsetType is required'))

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw BadRequestError when oauthConfigId is missing', async () => {
      const handler = updateToolsetOAuthConfig(appConfig)
      const req = createMockRequest({ params: { toolsetType: 'github' } })
      const res = createMockResponse()
      const next = sinon.stub()

      handleErrorStub.returns(new BadRequestError('oauthConfigId is required'))

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should call connector backend with PUT for OAuth config update', async () => {
      executeStub.resolves({ statusCode: 200, data: { updated: true } })
      const handler = updateToolsetOAuthConfig(appConfig)
      const req = createMockRequest({
        params: { toolsetType: 'github', oauthConfigId: 'oc-1' },
        body: { clientId: 'new-cid' },
      })
      const res = createMockResponse()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(handleResponseStub.calledOnce).to.be.true
    })
  })

  describe('deleteToolsetOAuthConfig', () => {
    it('should throw BadRequestError when toolsetType is missing', async () => {
      const handler = deleteToolsetOAuthConfig(appConfig)
      const req = createMockRequest({ params: { oauthConfigId: 'oc-1' } })
      const res = createMockResponse()
      const next = sinon.stub()

      handleErrorStub.returns(new BadRequestError('toolsetType is required'))

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw BadRequestError when oauthConfigId is missing', async () => {
      const handler = deleteToolsetOAuthConfig(appConfig)
      const req = createMockRequest({ params: { toolsetType: 'github' } })
      const res = createMockResponse()
      const next = sinon.stub()

      handleErrorStub.returns(new BadRequestError('oauthConfigId is required'))

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should call connector backend with DELETE', async () => {
      executeStub.resolves({ statusCode: 200, data: { deleted: true } })
      const handler = deleteToolsetOAuthConfig(appConfig)
      const req = createMockRequest({
        params: { toolsetType: 'github', oauthConfigId: 'oc-1' },
      })
      const res = createMockResponse()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(handleResponseStub.calledOnce).to.be.true
    })
  })

  describe('handleOAuthCallback - redirect handling', () => {
    it('should return JSON with redirectUrl on 302 response', async () => {
      executeStub.resolves({
        statusCode: 302,
        headers: { location: 'https://frontend/callback?success=true' },
        data: null,
      })

      const handler = handleOAuthCallback(appConfig)
      const req = createMockRequest({
        query: { code: 'auth-code', state: 'state-1' },
      })
      const res = createMockResponse()
      const next = sinon.stub()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.calledOnce).to.be.true
      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.redirectUrl).to.equal('https://frontend/callback?success=true')
    })

    it('should return JSON with redirectUrl on 307 response', async () => {
      executeStub.resolves({
        statusCode: 307,
        headers: { location: 'https://frontend/callback' },
        data: null,
      })

      const handler = handleOAuthCallback(appConfig)
      const req = createMockRequest({ query: { code: 'code' } })
      const res = createMockResponse()
      const next = sinon.stub()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should handle redirect_url in JSON response body', async () => {
      executeStub.resolves({
        statusCode: 200,
        data: { redirect_url: 'https://frontend/success' },
      })

      const handler = handleOAuthCallback(appConfig)
      const req = createMockRequest({ query: { code: 'code' } })
      const res = createMockResponse()
      const next = sinon.stub()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.redirectUrl).to.equal('https://frontend/success')
    })

    it('should reject invalid redirect URL from 302 location', async () => {
      executeStub.resolves({
        statusCode: 302,
        headers: { location: 'javascript:alert(1)' },
        data: null,
      })

      const handler = handleOAuthCallback(appConfig)
      const req = createMockRequest({ query: { code: 'code' } })
      const res = createMockResponse()
      const next = sinon.stub()

      handleErrorStub.returns(new BadRequestError('Invalid redirect URL'))

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should reject invalid redirect URL from JSON body', async () => {
      executeStub.resolves({
        statusCode: 200,
        data: { redirect_url: 'ftp://invalid' },
      })

      const handler = handleOAuthCallback(appConfig)
      const req = createMockRequest({ query: { code: 'code' } })
      const res = createMockResponse()
      const next = sinon.stub()

      handleErrorStub.returns(new BadRequestError('Invalid redirect URL'))

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should pass error query param to backend', async () => {
      executeStub.resolves({ statusCode: 200, data: { error: 'access_denied' } })

      const handler = handleOAuthCallback(appConfig)
      const req = createMockRequest({
        query: { error: 'access_denied', base_url: 'http://frontend' },
      })
      const res = createMockResponse()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(executeStub.calledOnce).to.be.true
    })
  })

  describe('reauthenticateToolsetInstance', () => {
    it('should throw BadRequestError when instanceId is missing', async () => {
      const handler = reauthenticateToolsetInstance(appConfig)
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = sinon.stub()

      handleErrorStub.returns(new BadRequestError('instanceId is required'))

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should call connector backend POST for reauthentication', async () => {
      executeStub.resolves({ statusCode: 200, data: { cleared: true } })
      const handler = reauthenticateToolsetInstance(appConfig)
      const req = createMockRequest({ params: { instanceId: 'inst-1' } })
      const res = createMockResponse()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(handleResponseStub.calledOnce).to.be.true
    })
  })

  describe('updateUserToolsetInstance', () => {
    it('should throw BadRequestError when instanceId is missing', async () => {
      const handler = updateUserToolsetInstance(appConfig)
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = sinon.stub()

      handleErrorStub.returns(new BadRequestError('instanceId is required'))

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  describe('removeToolsetCredentials', () => {
    it('should throw BadRequestError when instanceId is missing', async () => {
      const handler = removeToolsetCredentials(appConfig)
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = sinon.stub()

      handleErrorStub.returns(new BadRequestError('instanceId is required'))

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  describe('getMyToolsets', () => {
    it('should throw UnauthorizedError when userId is missing', async () => {
      const handler = getMyToolsets(appConfig)
      const req = createMockRequest({ user: {} })
      const res = createMockResponse()
      const next = sinon.stub()

      handleErrorStub.returns(new UnauthorizedError('User authentication required'))

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should pass search query param', async () => {
      executeStub.resolves({ statusCode: 200, data: [] })
      const handler = getMyToolsets(appConfig)
      const req = createMockRequest({
        query: { search: 'github' },
      })
      const res = createMockResponse()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(handleResponseStub.calledOnce).to.be.true
    })
  })

  describe('createToolsetInstance', () => {
    it('should throw UnauthorizedError when userId is missing', async () => {
      const handler = createToolsetInstance(appConfig)
      const req = createMockRequest({ user: {} })
      const res = createMockResponse()
      const next = sinon.stub()

      handleErrorStub.returns(new UnauthorizedError('required'))

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw BadRequestError when instanceName is missing', async () => {
      const handler = createToolsetInstance(appConfig)
      const req = createMockRequest({
        body: { toolsetType: 'github', authType: 'oauth' },
      })
      const res = createMockResponse()
      const next = sinon.stub()

      handleErrorStub.returns(new BadRequestError('instanceName is required'))

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw BadRequestError when toolsetType is missing', async () => {
      const handler = createToolsetInstance(appConfig)
      const req = createMockRequest({
        body: { instanceName: 'My Tool', authType: 'oauth' },
      })
      const res = createMockResponse()
      const next = sinon.stub()

      handleErrorStub.returns(new BadRequestError('toolsetType is required'))

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw BadRequestError when authType is missing', async () => {
      const handler = createToolsetInstance(appConfig)
      const req = createMockRequest({
        body: { instanceName: 'My Tool', toolsetType: 'github' },
      })
      const res = createMockResponse()
      const next = sinon.stub()

      handleErrorStub.returns(new BadRequestError('authType is required'))

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })
})
