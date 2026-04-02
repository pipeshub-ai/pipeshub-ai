import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as connectorUtils from '../../../../src/modules/tokens_manager/utils/connector.utils'
import * as connectorControllers from '../../../../src/modules/tokens_manager/controllers/connector.controllers'
import {
  getOAuthConfigRegistry,
  getOAuthConfigRegistryByType,
  getAllOAuthConfigs,
  createOAuthConfig,
  getOAuthConfig,
  updateOAuthConfig,
  deleteOAuthConfig,
} from '../../../../src/modules/tokens_manager/controllers/oauth.controllers'

describe('tokens_manager/controllers/oauth.controllers', () => {
  let mockAppConfig: any

  beforeEach(() => {
    mockAppConfig = {
      connectorBackend: 'http://connector-backend:8088',
    }
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('getOAuthConfigRegistry', () => {
    it('should return an async handler function', () => {
      const handler = getOAuthConfigRegistry(mockAppConfig)
      expect(handler).to.be.a('function')
    })

    it('should throw UnauthorizedError when userId is missing', async () => {
      const handler = getOAuthConfigRegistry(mockAppConfig)
      const req: any = { user: {}, query: {}, params: {}, headers: {} }
      const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub() }
      const next = sinon.stub()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call executeConnectorCommand for valid request', async () => {
      const handler = getOAuthConfigRegistry(mockAppConfig)
      sinon.stub(connectorControllers, 'isUserAdmin').resolves(false)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { configs: [] },
      })
      sinon.stub(connectorUtils, 'handleConnectorResponse')

      const req: any = {
        user: { userId: 'user-1' },
        query: {},
        params: {},
        headers: {},
      }
      const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub() }
      const next = sinon.stub()

      await handler(req, res, next)

      expect((connectorUtils.executeConnectorCommand as sinon.SinonStub).calledOnce).to.be.true
    })
  })

  describe('getOAuthConfigRegistryByType', () => {
    it('should return an async handler', () => {
      const handler = getOAuthConfigRegistryByType(mockAppConfig)
      expect(handler).to.be.a('function')
    })
  })

  describe('createOAuthConfig', () => {
    it('should call next with error when connectorType is missing', async () => {
      const handler = createOAuthConfig(mockAppConfig)
      const req: any = {
        user: { userId: 'user-1' },
        params: {},
        body: { oauthInstanceName: 'test', config: {}, baseUrl: 'http://test.com' },
        headers: {},
      }
      const res: any = {}
      const next = sinon.stub()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next with error when oauthInstanceName is missing', async () => {
      const handler = createOAuthConfig(mockAppConfig)
      const req: any = {
        user: { userId: 'user-1' },
        params: { connectorType: 'google' },
        body: { config: {}, baseUrl: 'http://test.com' },
        headers: {},
      }
      const res: any = {}
      const next = sinon.stub()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('getOAuthConfig', () => {
    it('should call next with error when configId is missing', async () => {
      const handler = getOAuthConfig(mockAppConfig)
      const req: any = {
        user: { userId: 'user-1' },
        params: { connectorType: 'google' },
        headers: {},
      }
      const res: any = {}
      const next = sinon.stub()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('updateOAuthConfig', () => {
    it('should call next with error when neither name nor config provided', async () => {
      const handler = updateOAuthConfig(mockAppConfig)
      const req: any = {
        user: { userId: 'user-1' },
        params: { connectorType: 'google', configId: 'cfg-1' },
        body: { baseUrl: 'http://test.com' },
        headers: {},
      }
      const res: any = {}
      const next = sinon.stub()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('deleteOAuthConfig', () => {
    it('should return an async handler', () => {
      const handler = deleteOAuthConfig(mockAppConfig)
      expect(handler).to.be.a('function')
    })
  })
})
