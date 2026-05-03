import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as connectorUtils from '../../../../src/modules/tokens_manager/utils/connector.utils'
import {
  isUserAdmin,
  getConnectorRegistry,
  getConnectorInstances,
  getActiveConnectorInstances,
  getInactiveConnectorInstances,
  getConfiguredConnectorInstances,
  createConnectorInstance,
  getConnectorInstance,
  getConnectorInstanceConfig,
  updateConnectorInstanceConfig,
  updateConnectorInstanceAuthConfig,
  updateConnectorInstanceFiltersSyncConfig,
  deleteConnectorInstance,
  updateConnectorInstanceName,
  getOAuthAuthorizationUrl,
  handleOAuthCallback,
  getConnectorInstanceFilterOptions,
  getFilterFieldOptions,
  saveConnectorInstanceFilterOptions,
  toggleConnectorInstance,
  getConnectorSchema,
  getActiveAgentInstances,
} from '../../../../src/modules/tokens_manager/controllers/connector.controllers'
import { UserGroups } from '../../../../src/modules/user_management/schema/userGroup.schema'

describe('tokens_manager/controllers/connector.controllers', () => {
  let mockAppConfig: any
  let req: any
  let res: any
  let next: sinon.SinonStub

  beforeEach(() => {
    mockAppConfig = {
      connectorBackend: 'http://connector-backend:8088',
    }

    req = {
      user: {
        userId: 'aaaaaaaaaaaaaaaaaaaaaaaa',
        orgId: 'bbbbbbbbbbbbbbbbbbbbbbbb',
      },
      params: {},
      query: {},
      body: {},
      headers: {},
    }

    res = {
      status: sinon.stub().returnsThis(),
      json: sinon.stub().returnsThis(),
      send: sinon.stub().returnsThis(),
    }

    next = sinon.stub()
  })

  afterEach(() => {
    sinon.restore()
  })

  // =========================================================================
  // isUserAdmin
  // =========================================================================
  describe('isUserAdmin', () => {
    it('should return true when user is in admin group', async () => {
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([{ type: 'admin' }]),
      } as any)

      const result = await isUserAdmin(req)
      expect(result).to.be.true
    })

    it('should return false when user is not in admin group', async () => {
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([{ type: 'standard' }]),
      } as any)

      const result = await isUserAdmin(req)
      expect(result).to.be.false
    })

    it('should return false when user has no groups', async () => {
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)

      const result = await isUserAdmin(req)
      expect(result).to.be.false
    })

    it('should throw UnauthorizedError when userId is missing', async () => {
      req.user = {}
      try {
        await isUserAdmin(req)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.equal('User authentication required')
      }
    })
  })

  // =========================================================================
  // getConnectorRegistry
  // =========================================================================
  describe('getConnectorRegistry', () => {
    it('should return an async handler function', () => {
      const handler = getConnectorRegistry(mockAppConfig)
      expect(handler).to.be.a('function')
    })

    it('should throw UnauthorizedError when userId is missing', async () => {
      const handler = getConnectorRegistry(mockAppConfig)
      req.user = {}
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should call executeConnectorCommand and return data for valid request', async () => {
      const handler = getConnectorRegistry(mockAppConfig)
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: [{ connectorType: 'google_drive' }],
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.calledOnce).to.be.true
    })

    it('should pass query params correctly', async () => {
      const handler = getConnectorRegistry(mockAppConfig)
      req.query = { scope: 'org', page: '1', limit: '10', search: 'google' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      const execStub = sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: [],
      })

      await handler(req, res, next)

      const calledUrl = execStub.firstCall.args[0] as string
      expect(calledUrl).to.include('scope=org')
      expect(calledUrl).to.include('page=1')
      expect(calledUrl).to.include('limit=10')
      expect(calledUrl).to.include('search=google')
    })

    it('should call next with handled error on failure', async () => {
      const handler = getConnectorRegistry(mockAppConfig)
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').rejects(
        new Error('Connection refused')
      )

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // =========================================================================
  // getConnectorInstances
  // =========================================================================
  describe('getConnectorInstances', () => {
    it('should return an async handler function', () => {
      const handler = getConnectorInstances(mockAppConfig)
      expect(handler).to.be.a('function')
    })

    it('should throw UnauthorizedError when userId is missing', async () => {
      const handler = getConnectorInstances(mockAppConfig)
      req.user = {}
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw BadRequestError when scope is missing', async () => {
      const handler = getConnectorInstances(mockAppConfig)
      req.query = {}
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should return connector instances for valid request', async () => {
      const handler = getConnectorInstances(mockAppConfig)
      req.query = { scope: 'org' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: [{ id: 'c1', connectorType: 'google_drive' }],
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // =========================================================================
  // getActiveConnectorInstances
  // =========================================================================
  describe('getActiveConnectorInstances', () => {
    it('should return an async handler function', () => {
      const handler = getActiveConnectorInstances(mockAppConfig)
      expect(handler).to.be.a('function')
    })

    it('should throw UnauthorizedError when userId is missing', async () => {
      const handler = getActiveConnectorInstances(mockAppConfig)
      req.user = {}
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should return active connector instances', async () => {
      const handler = getActiveConnectorInstances(mockAppConfig)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: [{ id: 'c1', isActive: true }],
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // =========================================================================
  // getInactiveConnectorInstances
  // =========================================================================
  describe('getInactiveConnectorInstances', () => {
    it('should return an async handler function', () => {
      const handler = getInactiveConnectorInstances(mockAppConfig)
      expect(handler).to.be.a('function')
    })

    it('should throw UnauthorizedError when userId is missing', async () => {
      const handler = getInactiveConnectorInstances(mockAppConfig)
      req.user = {}
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should return inactive connector instances', async () => {
      const handler = getInactiveConnectorInstances(mockAppConfig)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: [{ id: 'c2', isActive: false }],
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // =========================================================================
  // getConfiguredConnectorInstances
  // =========================================================================
  describe('getConfiguredConnectorInstances', () => {
    it('should return an async handler function', () => {
      const handler = getConfiguredConnectorInstances(mockAppConfig)
      expect(handler).to.be.a('function')
    })

    it('should throw UnauthorizedError when userId is missing', async () => {
      const handler = getConfiguredConnectorInstances(mockAppConfig)
      req.user = {}
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should return configured connector instances', async () => {
      const handler = getConfiguredConnectorInstances(mockAppConfig)
      req.query = { scope: 'org' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: [{ id: 'c1', isConfigured: true }],
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // =========================================================================
  // createConnectorInstance
  // =========================================================================
  describe('createConnectorInstance', () => {
    it('should return an async handler function', () => {
      const handler = createConnectorInstance(mockAppConfig)
      expect(handler).to.be.a('function')
    })

    it('should throw UnauthorizedError when userId is missing', async () => {
      const handler = createConnectorInstance(mockAppConfig)
      req.user = {}
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw BadRequestError when connectorType is missing', async () => {
      const handler = createConnectorInstance(mockAppConfig)
      req.body = { instanceName: 'My Instance' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should throw BadRequestError when instanceName is missing', async () => {
      const handler = createConnectorInstance(mockAppConfig)
      req.body = { connectorType: 'google_drive' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should create connector instance for valid request', async () => {
      const handler = createConnectorInstance(mockAppConfig)
      req.body = {
        connectorType: 'google_drive',
        instanceName: 'My Drive',
        config: {},
        scope: 'org',
      }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([{ type: 'admin' }]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 201,
        data: { id: 'new-connector-id' },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(201)).to.be.true
    })
  })

  // =========================================================================
  // getConnectorInstance
  // =========================================================================
  describe('getConnectorInstance', () => {
    it('should return an async handler function', () => {
      const handler = getConnectorInstance(mockAppConfig)
      expect(handler).to.be.a('function')
    })

    it('should throw BadRequestError when connectorId is missing', async () => {
      const handler = getConnectorInstance(mockAppConfig)
      req.params = {}
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should return connector instance for valid request', async () => {
      const handler = getConnectorInstance(mockAppConfig)
      req.params = { connectorId: 'c1' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { id: 'c1', connectorType: 'google_drive' },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // =========================================================================
  // getConnectorInstanceConfig
  // =========================================================================
  describe('getConnectorInstanceConfig', () => {
    it('should return an async handler function', () => {
      const handler = getConnectorInstanceConfig(mockAppConfig)
      expect(handler).to.be.a('function')
    })

    it('should throw BadRequestError when connectorId is missing', async () => {
      const handler = getConnectorInstanceConfig(mockAppConfig)
      req.params = {}
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should return connector config for valid request', async () => {
      const handler = getConnectorInstanceConfig(mockAppConfig)
      req.params = { connectorId: 'c1' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { auth: {}, sync: {} },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // =========================================================================
  // updateConnectorInstanceConfig
  // =========================================================================
  describe('updateConnectorInstanceConfig', () => {
    it('should return an async handler function', () => {
      const handler = updateConnectorInstanceConfig(mockAppConfig)
      expect(handler).to.be.a('function')
    })

    it('should throw BadRequestError when connectorId is missing', async () => {
      const handler = updateConnectorInstanceConfig(mockAppConfig)
      req.params = {}
      req.body = { auth: {} }
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should update connector config for valid request', async () => {
      const handler = updateConnectorInstanceConfig(mockAppConfig)
      req.params = { connectorId: 'c1' }
      req.body = { auth: { type: 'oauth' }, sync: { interval: 60 } }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([{ type: 'admin' }]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { message: 'Updated' },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // =========================================================================
  // updateConnectorInstanceAuthConfig
  // =========================================================================
  describe('updateConnectorInstanceAuthConfig', () => {
    it('should return an async handler function', () => {
      const handler = updateConnectorInstanceAuthConfig(mockAppConfig)
      expect(handler).to.be.a('function')
    })

    it('should throw BadRequestError when connectorId is missing', async () => {
      const handler = updateConnectorInstanceAuthConfig(mockAppConfig)
      req.params = {}
      req.body = { auth: {} }
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw BadRequestError when auth is missing in body', async () => {
      const handler = updateConnectorInstanceAuthConfig(mockAppConfig)
      req.params = { connectorId: 'c1' }
      req.body = {}
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should update auth config for valid request', async () => {
      const handler = updateConnectorInstanceAuthConfig(mockAppConfig)
      req.params = { connectorId: 'c1' }
      req.body = { auth: { type: 'oauth' }, baseUrl: 'http://example.com' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([{ type: 'admin' }]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { message: 'Auth updated' },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // =========================================================================
  // updateConnectorInstanceFiltersSyncConfig
  // =========================================================================
  describe('updateConnectorInstanceFiltersSyncConfig', () => {
    it('should return an async handler function', () => {
      const handler = updateConnectorInstanceFiltersSyncConfig(mockAppConfig)
      expect(handler).to.be.a('function')
    })

    it('should throw BadRequestError when sync and filters are both missing', async () => {
      const handler = updateConnectorInstanceFiltersSyncConfig(mockAppConfig)
      req.params = { connectorId: 'c1' }
      req.body = {}
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should update filters-sync config when sync is provided', async () => {
      const handler = updateConnectorInstanceFiltersSyncConfig(mockAppConfig)
      req.params = { connectorId: 'c1' }
      req.body = { sync: { interval: 30 } }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { message: 'Updated' },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should update filters-sync config when filters is provided', async () => {
      const handler = updateConnectorInstanceFiltersSyncConfig(mockAppConfig)
      req.params = { connectorId: 'c1' }
      req.body = { filters: { include: ['docs'] } }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { message: 'Updated' },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // =========================================================================
  // deleteConnectorInstance
  // =========================================================================
  describe('deleteConnectorInstance', () => {
    it('should return an async handler function', () => {
      const handler = deleteConnectorInstance(mockAppConfig)
      expect(handler).to.be.a('function')
    })

    it('should throw BadRequestError when connectorId is missing', async () => {
      const handler = deleteConnectorInstance(mockAppConfig)
      req.params = {}
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should delete connector instance for valid request', async () => {
      const handler = deleteConnectorInstance(mockAppConfig)
      req.params = { connectorId: 'c1' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([{ type: 'admin' }]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { message: 'Deleted' },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // =========================================================================
  // updateConnectorInstanceName
  // =========================================================================
  describe('updateConnectorInstanceName', () => {
    it('should return an async handler function', () => {
      const handler = updateConnectorInstanceName(mockAppConfig)
      expect(handler).to.be.a('function')
    })

    it('should throw BadRequestError when connectorId is missing', async () => {
      const handler = updateConnectorInstanceName(mockAppConfig)
      req.params = {}
      req.body = { instanceName: 'New Name' }
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw BadRequestError when instanceName is empty', async () => {
      const handler = updateConnectorInstanceName(mockAppConfig)
      req.params = { connectorId: 'c1' }
      req.body = { instanceName: '' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should throw BadRequestError when instanceName is whitespace only', async () => {
      const handler = updateConnectorInstanceName(mockAppConfig)
      req.params = { connectorId: 'c1' }
      req.body = { instanceName: '   ' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should update connector name for valid request', async () => {
      const handler = updateConnectorInstanceName(mockAppConfig)
      req.params = { connectorId: 'c1' }
      req.body = { instanceName: 'New Name' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { message: 'Name updated' },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // =========================================================================
  // getOAuthAuthorizationUrl
  // =========================================================================
  describe('getOAuthAuthorizationUrl', () => {
    it('should return an async handler function', () => {
      const handler = getOAuthAuthorizationUrl(mockAppConfig)
      expect(handler).to.be.a('function')
    })

    it('should throw BadRequestError when connectorId is missing', async () => {
      const handler = getOAuthAuthorizationUrl(mockAppConfig)
      req.params = {}
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should return OAuth URL for valid request', async () => {
      const handler = getOAuthAuthorizationUrl(mockAppConfig)
      req.params = { connectorId: 'c1' }
      req.query = { baseUrl: 'http://example.com' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { authorizationUrl: 'https://oauth.example.com/auth' },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should handle baseUrl query param', async () => {
      const handler = getOAuthAuthorizationUrl(mockAppConfig)
      req.params = { connectorId: 'c1' }
      req.query = { baseUrl: 'http://custom.com' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      const execStub = sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { authorizationUrl: 'https://oauth.example.com/auth' },
      })

      await handler(req, res, next)

      const calledUrl = execStub.firstCall.args[0] as string
      expect(calledUrl).to.include('base_url=http')
    })
  })

  // =========================================================================
  // handleOAuthCallback
  // =========================================================================
  describe('handleOAuthCallback', () => {
    it('should return an async handler function', () => {
      const handler = handleOAuthCallback(mockAppConfig)
      expect(handler).to.be.a('function')
    })

    it('should throw BadRequestError when code is missing', async () => {
      const handler = handleOAuthCallback(mockAppConfig)
      req.query = { state: 'some-state' }
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw BadRequestError when state is missing', async () => {
      const handler = handleOAuthCallback(mockAppConfig)
      req.query = { code: 'auth-code' }
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should handle 302 redirect response', async () => {
      const handler = handleOAuthCallback(mockAppConfig)
      req.query = { code: 'auth-code', state: 'some-state' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 302,
        headers: { location: 'https://app.example.com/callback' },
        data: null,
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.calledOnce).to.be.true
      expect(res.json.firstCall.args[0]).to.have.property('redirectUrl')
    })

    it('should handle response with redirect_url in data', async () => {
      const handler = handleOAuthCallback(mockAppConfig)
      req.query = { code: 'auth-code', state: 'some-state' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { redirect_url: 'https://app.example.com/success' },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0].redirectUrl).to.equal(
        'https://app.example.com/success',
      )
    })

    it('should handle normal response without redirect', async () => {
      const handler = handleOAuthCallback(mockAppConfig)
      req.query = { code: 'auth-code', state: 'some-state' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { message: 'OAuth completed' },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // =========================================================================
  // getConnectorInstanceFilterOptions
  // =========================================================================
  describe('getConnectorInstanceFilterOptions', () => {
    it('should return an async handler function', () => {
      const handler = getConnectorInstanceFilterOptions(mockAppConfig)
      expect(handler).to.be.a('function')
    })

    it('should throw BadRequestError when connectorId is missing', async () => {
      const handler = getConnectorInstanceFilterOptions(mockAppConfig)
      req.params = {}
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should return filter options for valid request', async () => {
      const handler = getConnectorInstanceFilterOptions(mockAppConfig)
      req.params = { connectorId: 'c1' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { filters: [{ key: 'mimetype', values: ['pdf'] }] },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // =========================================================================
  // getFilterFieldOptions
  // =========================================================================
  describe('getFilterFieldOptions', () => {
    it('should return an async handler function', () => {
      const handler = getFilterFieldOptions(mockAppConfig)
      expect(handler).to.be.a('function')
    })

    it('should throw BadRequestError when connectorId is missing', async () => {
      const handler = getFilterFieldOptions(mockAppConfig)
      req.params = { filterKey: 'mimetype' }
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw BadRequestError when filterKey is missing', async () => {
      const handler = getFilterFieldOptions(mockAppConfig)
      req.params = { connectorId: 'c1' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should return filter field options for valid request', async () => {
      const handler = getFilterFieldOptions(mockAppConfig)
      req.params = { connectorId: 'c1', filterKey: 'mimetype' }
      req.query = { page: '1', limit: '20', search: 'pdf', cursor: 'abc' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { options: ['application/pdf'] },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // =========================================================================
  // saveConnectorInstanceFilterOptions
  // =========================================================================
  describe('saveConnectorInstanceFilterOptions', () => {
    it('should return an async handler function', () => {
      const handler = saveConnectorInstanceFilterOptions(mockAppConfig)
      expect(handler).to.be.a('function')
    })

    it('should throw BadRequestError when connectorId is missing', async () => {
      const handler = saveConnectorInstanceFilterOptions(mockAppConfig)
      req.params = {}
      req.body = { filters: {} }
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw BadRequestError when filters is missing', async () => {
      const handler = saveConnectorInstanceFilterOptions(mockAppConfig)
      req.params = { connectorId: 'c1' }
      req.body = {}
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should save filter options for valid request', async () => {
      const handler = saveConnectorInstanceFilterOptions(mockAppConfig)
      req.params = { connectorId: 'c1' }
      req.body = { filters: { mimetype: ['pdf'] } }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([{ type: 'admin' }]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { message: 'Filters saved' },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // =========================================================================
  // toggleConnectorInstance
  // =========================================================================
  describe('toggleConnectorInstance', () => {
    it('should return an async handler function', () => {
      const handler = toggleConnectorInstance(mockAppConfig)
      expect(handler).to.be.a('function')
    })

    it('should throw BadRequestError when connectorId is missing', async () => {
      const handler = toggleConnectorInstance(mockAppConfig)
      req.params = {}
      req.body = { type: 'activate' }
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw BadRequestError when type is missing', async () => {
      const handler = toggleConnectorInstance(mockAppConfig)
      req.params = { connectorId: 'c1' }
      req.body = {}
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should toggle connector for valid request', async () => {
      const handler = toggleConnectorInstance(mockAppConfig)
      req.params = { connectorId: 'c1' }
      req.body = { type: 'activate', fullSync: true }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([{ type: 'admin' }]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { message: 'Toggled' },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should toggle without fullSync when not boolean', async () => {
      const handler = toggleConnectorInstance(mockAppConfig)
      req.params = { connectorId: 'c1' }
      req.body = { type: 'deactivate' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { message: 'Toggled' },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // =========================================================================
  // getConnectorSchema
  // =========================================================================
  describe('getConnectorSchema', () => {
    it('should return an async handler function', () => {
      const handler = getConnectorSchema(mockAppConfig)
      expect(handler).to.be.a('function')
    })

    it('should throw BadRequestError when connectorType is missing', async () => {
      const handler = getConnectorSchema(mockAppConfig)
      req.params = {}
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should return schema for valid request', async () => {
      const handler = getConnectorSchema(mockAppConfig)
      req.params = { connectorType: 'google_drive' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { schema: { auth: {}, sync: {} } },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // =========================================================================
  // getActiveAgentInstances
  // =========================================================================
  describe('getActiveAgentInstances', () => {
    it('should return an async handler function', () => {
      const handler = getActiveAgentInstances(mockAppConfig)
      expect(handler).to.be.a('function')
    })

    it('should throw UnauthorizedError when userId is missing', async () => {
      const handler = getActiveAgentInstances(mockAppConfig)
      req.user = {}
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should return active agent instances', async () => {
      const handler = getActiveAgentInstances(mockAppConfig)
      req.query = { scope: 'org' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([{ type: 'admin' }]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: [{ id: 'agent-1' }],
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // =========================================================================
  // Branch coverage: query param branches across multiple methods
  // =========================================================================
  describe('getConnectorRegistry - query param branches', () => {
    it('should pass all query params when provided', async () => {
      const handler = getConnectorRegistry(mockAppConfig)
      req.query = { scope: 'org', page: '1', limit: '10', search: 'google' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: [],
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should work with no query params', async () => {
      const handler = getConnectorRegistry(mockAppConfig)
      req.query = {}
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: [],
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should work with only scope', async () => {
      const handler = getConnectorRegistry(mockAppConfig)
      req.query = { scope: 'user' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: [],
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should set X-Is-Admin to true when user is admin', async () => {
      const handler = getConnectorRegistry(mockAppConfig)
      req.query = {}
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([{ type: 'admin' }]),
      } as any)
      const execStub = sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: [],
      })

      await handler(req, res, next)

      const headers = execStub.firstCall.args[2]
      expect(headers['X-Is-Admin']).to.equal('true')
    })

    it('should set X-Is-Admin to false when user is not admin', async () => {
      const handler = getConnectorRegistry(mockAppConfig)
      req.query = {}
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([{ type: 'member' }]),
      } as any)
      const execStub = sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: [],
      })

      await handler(req, res, next)

      const headers = execStub.firstCall.args[2]
      expect(headers['X-Is-Admin']).to.equal('false')
    })
  })

  describe('getConnectorInstances - query param branches', () => {
    it('should pass all query params', async () => {
      const handler = getConnectorInstances(mockAppConfig)
      req.query = { scope: 'org', page: '1', limit: '10', search: 'slack' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: [],
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should work with only scope (required)', async () => {
      const handler = getConnectorInstances(mockAppConfig)
      req.query = { scope: 'org' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: [],
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should throw when scope is missing', async () => {
      const handler = getConnectorInstances(mockAppConfig)
      req.query = {}

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('getConfiguredConnectorInstances - query param branches', () => {
    it('should pass all query params', async () => {
      const handler = getConfiguredConnectorInstances(mockAppConfig)
      req.query = { scope: 'org', page: '1', limit: '10', search: 'jira' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: [],
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should work with no query params', async () => {
      const handler = getConfiguredConnectorInstances(mockAppConfig)
      req.query = {}
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: [],
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should work with only scope', async () => {
      const handler = getConfiguredConnectorInstances(mockAppConfig)
      req.query = { scope: 'user' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: [],
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  describe('getActiveAgentInstances - query param branches', () => {
    it('should pass all query params', async () => {
      const handler = getActiveAgentInstances(mockAppConfig)
      req.query = { scope: 'org', page: '1', limit: '10', search: 'agent' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: [],
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should work with no query params', async () => {
      const handler = getActiveAgentInstances(mockAppConfig)
      req.query = {}
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: [],
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // =========================================================================
  // Branch coverage: handleOAuthCallback - redirect branches
  // =========================================================================
  describe('handleOAuthCallback - redirect branches', () => {
    it('should handle 302 redirect with location header', async () => {
      const handler = handleOAuthCallback(mockAppConfig)
      req.query = { code: 'auth-code', state: 'state-token' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 302,
        headers: { location: 'http://redirect-url.com' },
        data: null,
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({ redirectUrl: 'http://redirect-url.com' })
    })

    it('should handle JSON response with redirect_url', async () => {
      const handler = handleOAuthCallback(mockAppConfig)
      req.query = { code: 'auth-code', state: 'state-token' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { redirect_url: 'http://json-redirect.com' },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({ redirectUrl: 'http://json-redirect.com' })
    })

    it('should handle normal response without redirect', async () => {
      const handler = handleOAuthCallback(mockAppConfig)
      req.query = { code: 'auth-code', state: 'state-token' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { success: true },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should pass error and baseUrl query params when provided', async () => {
      const handler = handleOAuthCallback(mockAppConfig)
      req.query = { code: 'auth-code', state: 'state-token', error: 'access_denied', baseUrl: 'http://base.com' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { success: true },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should handle 302 without location header - fall through to JSON check', async () => {
      const handler = handleOAuthCallback(mockAppConfig)
      req.query = { code: 'auth-code', state: 'state-token' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 302,
        headers: {},
        data: { redirect_url: 'http://fallback.com' },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should handle data without redirect_url - fall through to handleConnectorResponse', async () => {
      const handler = handleOAuthCallback(mockAppConfig)
      req.query = { code: 'auth-code', state: 'state-token' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { some: 'data', redirect_url: undefined },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // =========================================================================
  // Branch coverage: getOAuthAuthorizationUrl - baseUrl branch
  // =========================================================================
  describe('getOAuthAuthorizationUrl - baseUrl branch', () => {
    it('should include baseUrl when provided', async () => {
      const handler = getOAuthAuthorizationUrl(mockAppConfig)
      req.params = { connectorId: 'conn-1' }
      req.query = { baseUrl: 'http://custom.base.com' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { url: 'http://oauth.com/auth' },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should work without baseUrl', async () => {
      const handler = getOAuthAuthorizationUrl(mockAppConfig)
      req.params = { connectorId: 'conn-1' }
      req.query = {}
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { url: 'http://oauth.com/auth' },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // =========================================================================
  // Branch coverage: toggleConnectorInstance - fullSync boolean branch
  // =========================================================================
  describe('toggleConnectorInstance - fullSync branch', () => {
    it('should include fullSync when it is a boolean true', async () => {
      const handler = toggleConnectorInstance(mockAppConfig)
      req.params = { connectorId: 'conn-1' }
      req.body = { type: 'activate', fullSync: true }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      const execStub = sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { active: true },
      })

      await handler(req, res, next)

      const body = execStub.firstCall.args[3]
      expect(body).to.have.property('fullSync', true)
    })

    it('should include fullSync when it is a boolean false', async () => {
      const handler = toggleConnectorInstance(mockAppConfig)
      req.params = { connectorId: 'conn-1' }
      req.body = { type: 'activate', fullSync: false }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      const execStub = sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { active: true },
      })

      await handler(req, res, next)

      const body = execStub.firstCall.args[3]
      expect(body).to.have.property('fullSync', false)
    })

    it('should not include fullSync when it is not a boolean', async () => {
      const handler = toggleConnectorInstance(mockAppConfig)
      req.params = { connectorId: 'conn-1' }
      req.body = { type: 'activate', fullSync: 'yes' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      const execStub = sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { active: true },
      })

      await handler(req, res, next)

      const body = execStub.firstCall.args[3]
      expect(body).to.not.have.property('fullSync')
    })

    it('should not include fullSync when it is undefined', async () => {
      const handler = toggleConnectorInstance(mockAppConfig)
      req.params = { connectorId: 'conn-1' }
      req.body = { type: 'activate' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      const execStub = sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { active: true },
      })

      await handler(req, res, next)

      const body = execStub.firstCall.args[3]
      expect(body).to.not.have.property('fullSync')
    })
  })

  // =========================================================================
  // Branch coverage: updateConnectorInstanceName - empty instanceName
  // =========================================================================
  describe('updateConnectorInstanceName - instanceName.trim() branches', () => {
    it('should throw when instanceName is empty string after trim', async () => {
      const handler = updateConnectorInstanceName(mockAppConfig)
      req.params = { connectorId: 'conn-1' }
      req.body = { instanceName: '   ' }

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should throw when instanceName is missing', async () => {
      const handler = updateConnectorInstanceName(mockAppConfig)
      req.params = { connectorId: 'conn-1' }
      req.body = {}

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // =========================================================================
  // Branch coverage: getFilterFieldOptions - query params and filterKey
  // =========================================================================
  describe('getFilterFieldOptions - query param and filterKey branches', () => {
    it('should pass all query params including cursor', async () => {
      const handler = getFilterFieldOptions(mockAppConfig)
      req.params = { connectorId: 'conn-1', filterKey: 'department' }
      req.query = { page: '1', limit: '10', search: 'eng', cursor: 'abc123' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: [{ value: 'Engineering' }],
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should work with no query params', async () => {
      const handler = getFilterFieldOptions(mockAppConfig)
      req.params = { connectorId: 'conn-1', filterKey: 'department' }
      req.query = {}
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: [],
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should throw when filterKey is missing', async () => {
      const handler = getFilterFieldOptions(mockAppConfig)
      req.params = { connectorId: 'conn-1' }
      req.query = {}
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should work with only cursor', async () => {
      const handler = getFilterFieldOptions(mockAppConfig)
      req.params = { connectorId: 'conn-1', filterKey: 'type' }
      req.query = { cursor: 'xyz' }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: [],
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // =========================================================================
  // Branch coverage: createConnectorInstance - connectorType/instanceName
  // =========================================================================
  describe('createConnectorInstance - validation branches', () => {
    it('should throw when connectorType is missing', async () => {
      const handler = createConnectorInstance(mockAppConfig)
      req.body = { instanceName: 'My Connector' }

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should throw when instanceName is missing', async () => {
      const handler = createConnectorInstance(mockAppConfig)
      req.body = { connectorType: 'google_drive' }

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // =========================================================================
  // Branch coverage: updateConnectorInstanceFiltersSyncConfig - validation
  // =========================================================================
  describe('updateConnectorInstanceFiltersSyncConfig - validation branches', () => {
    it('should throw when both sync and filters are missing', async () => {
      const handler = updateConnectorInstanceFiltersSyncConfig(mockAppConfig)
      req.params = { connectorId: 'conn-1' }
      req.body = {}
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should succeed when only sync is provided', async () => {
      const handler = updateConnectorInstanceFiltersSyncConfig(mockAppConfig)
      req.params = { connectorId: 'conn-1' }
      req.body = { sync: { interval: 60 } }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { success: true },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should succeed when only filters is provided', async () => {
      const handler = updateConnectorInstanceFiltersSyncConfig(mockAppConfig)
      req.params = { connectorId: 'conn-1' }
      req.body = { filters: { departments: ['eng'] } }
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([]),
      } as any)
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { success: true },
      })

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })
})
