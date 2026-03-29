import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createUserRouter } from '../../../../src/modules/user_management/routes/users.routes'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { Logger } from '../../../../src/libs/services/logger.service'
import { UserController } from '../../../../src/modules/user_management/controller/users.controller'
import { MailService } from '../../../../src/modules/user_management/services/mail.service'
import { AuthService } from '../../../../src/modules/user_management/services/auth.service'
import { EntitiesEventProducer } from '../../../../src/modules/user_management/services/entity_events.service'
import { OrgController } from '../../../../src/modules/user_management/controller/org.controller'
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config'
import { PrometheusService } from '../../../../src/libs/services/prometheus/prometheus.service'

describe('User Routes - handler coverage', () => {
  let container: Container
  let mockUserController: any
  let router: any

  beforeEach(() => {
    container = new Container()

    const mockAuthMiddleware = {
      authenticate: sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      scopedTokenValidator: sinon.stub().returns(
        sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      ),
    }

    const mockLogger = {
      debug: sinon.stub(), info: sinon.stub(), error: sinon.stub(), warn: sinon.stub(),
    }

    const mockConfig = {
      frontendUrl: 'http://localhost:3000',
      scopedJwtSecret: 'test-secret',
      cmBackend: 'http://localhost:3004',
      connectorBackend: 'http://localhost:8088',
    }

    mockUserController = {
      getAllUsers: sinon.stub().resolves(),
      getAllUsersWithGroups: sinon.stub().resolves(),
      getUserById: sinon.stub().resolves(),
      getUserEmailByUserId: sinon.stub().resolves(),
      getUsersByIds: sinon.stub().resolves(),
      checkUserExistsByEmail: sinon.stub().resolves(),
      createUser: sinon.stub().resolves(),
      updateUser: sinon.stub().resolves(),
      updateFullName: sinon.stub().resolves(),
      updateFirstName: sinon.stub().resolves(),
      updateLastName: sinon.stub().resolves(),
      updateDesignation: sinon.stub().resolves(),
      updateEmail: sinon.stub().resolves(),
      deleteUser: sinon.stub().resolves(),
      updateUserDisplayPicture: sinon.stub().resolves(),
      getUserDisplayPicture: sinon.stub().resolves(),
      removeUserDisplayPicture: sinon.stub().resolves(),
      resendInvite: sinon.stub().resolves(),
      addManyUsers: sinon.stub().resolves(),
      listUsers: sinon.stub().resolves(),
      getUserTeams: sinon.stub().resolves(),
      unblockUser: sinon.stub().resolves(),
    }

    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any)
    container.bind<Logger>('Logger').toConstantValue(mockLogger as any)
    container.bind<AppConfig>('AppConfig').toConstantValue(mockConfig as any)
    container.bind<UserController>('UserController').toConstantValue(mockUserController as any)
    container.bind<MailService>('MailService').toConstantValue({ sendMail: sinon.stub() } as any)
    container.bind<AuthService>('AuthService').toConstantValue({ passwordMethodEnabled: sinon.stub() } as any)
    container.bind<EntitiesEventProducer>('EntitiesEventProducer').toConstantValue({ publishEvent: sinon.stub() } as any)
    container.bind<OrgController>('OrgController').toConstantValue({ createOrg: sinon.stub() } as any)
    container.bind<any>(PrometheusService).toConstantValue({ recordActivity: sinon.stub() })

    router = createUserRouter(container)
  })

  afterEach(() => {
    sinon.restore()
  })

  function findHandler(path: string, method: string) {
    const layer = router.stack.find(
      (l: any) => l.route && l.route.path === path && l.route.methods[method],
    )
    if (!layer) return null
    return layer.route.stack[layer.route.stack.length - 1].handle
  }

  function mockRes() {
    const res: any = {
      status: sinon.stub().returnsThis(),
      json: sinon.stub().returnsThis(),
      send: sinon.stub().returnsThis(),
    }
    return res
  }

  describe('GET / handler', () => {
    it('should call userController.getAllUsers', async () => {
      const handler = findHandler('/', 'get')
      expect(handler).to.exist

      const req = { user: { userId: 'user1', orgId: 'org1' } } as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockUserController.getAllUsers.calledOnce).to.be.true
    })

    it('should call next on error', async () => {
      mockUserController.getAllUsers.rejects(new Error('DB error'))
      // Re-bind with throwing controller
      container.rebind<UserController>('UserController').toConstantValue(mockUserController as any)
      router = createUserRouter(container)

      const handler = findHandler('/', 'get')
      const req = {} as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  describe('GET /fetch/with-groups handler', () => {
    it('should call userController.getAllUsersWithGroups', async () => {
      const handler = findHandler('/fetch/with-groups', 'get')
      expect(handler).to.exist

      const req = {} as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockUserController.getAllUsersWithGroups.calledOnce).to.be.true
    })
  })

  describe('PUT /:id/unblock handler', () => {
    it('should call userController.unblockUser', async () => {
      const handler = findHandler('/:id/unblock', 'put')
      expect(handler).to.exist

      const req = { params: { id: '507f1f77bcf86cd799439011' } } as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockUserController.unblockUser.calledOnce).to.be.true
    })
  })

  describe('POST /by-ids handler', () => {
    it('should call userController.getUsersByIds', async () => {
      const handler = findHandler('/by-ids', 'post')
      expect(handler).to.exist

      const req = { body: { userIds: ['507f1f77bcf86cd799439011'] } } as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockUserController.getUsersByIds.calledOnce).to.be.true
    })
  })

  describe('GET /health handler', () => {
    it('should return health status', () => {
      const handler = findHandler('/health', 'get')
      expect(handler).to.exist

      const req = {} as any
      const res = mockRes()

      handler(req, res)
      expect(res.json.calledOnce).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.status).to.equal('healthy')
      expect(response.timestamp).to.be.a('string')
    })
  })

  describe('GET /:id/adminCheck handler', () => {
    it('should return admin access message', async () => {
      const handler = findHandler('/:id/adminCheck', 'get')
      expect(handler).to.exist

      const req = { params: { id: '507f1f77bcf86cd799439011' } } as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.calledOnce).to.be.true
    })
  })

  describe('POST /updateAppConfig handler', () => {
    it('should update app config and rebind services', async () => {
      const handler = findHandler('/updateAppConfig', 'post')
      expect(handler).to.exist

      // Stub loadAppConfig
      const configModule = require('../../../../src/modules/tokens_manager/config/config')
      sinon.stub(configModule, 'loadAppConfig').resolves({
        frontendUrl: 'http://localhost:3000',
        scopedJwtSecret: 'updated-secret',
        cmBackend: 'http://localhost:3004',
        connectorBackend: 'http://localhost:8088',
      })

      const req = { tokenPayload: { orgId: 'org1' } } as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(res.status.calledWith(200)).to.be.true
    })

    it('should call next on error', async () => {
      const handler = findHandler('/updateAppConfig', 'post')

      const configModule = require('../../../../src/modules/tokens_manager/config/config')
      sinon.stub(configModule, 'loadAppConfig').rejects(new Error('Config load failed'))

      const req = { tokenPayload: { orgId: 'org1' } } as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  describe('GET /graph/list handler', () => {
    it('should call userController.listUsers', async () => {
      const handler = findHandler('/graph/list', 'get')
      expect(handler).to.exist

      const req = {} as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockUserController.listUsers.calledOnce).to.be.true
    })
  })

  describe('GET /teams/list handler', () => {
    it('should call userController.getUserTeams', async () => {
      const handler = findHandler('/teams/list', 'get')
      expect(handler).to.exist

      const req = {} as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockUserController.getUserTeams.calledOnce).to.be.true
    })
  })

  describe('additional route registrations', () => {
    it('should register PATCH /:id/fullname route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/:id/fullname' && l.route.methods.patch,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register PATCH /:id/firstName route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/:id/firstName' && l.route.methods.patch,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register PATCH /:id/lastName route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/:id/lastName' && l.route.methods.patch,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register PATCH /:id/designation route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/:id/designation' && l.route.methods.patch,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register PATCH /:id/email route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/:id/email' && l.route.methods.patch,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register PUT /dp route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/dp' && l.route.methods.put,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register DELETE /dp route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/dp' && l.route.methods.delete,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register GET /dp route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/dp' && l.route.methods.get,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register DELETE /:id route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/:id' && l.route.methods.delete,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register POST /bulk/invite route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/bulk/invite' && l.route.methods.post,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register POST /:id/resend-invite route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/:id/resend-invite' && l.route.methods.post,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register GET /internal/admin-users route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/internal/admin-users' && l.route.methods.get,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register GET /internal/:id route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/internal/:id' && l.route.methods.get,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register GET /email/exists route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/email/exists' && l.route.methods.get,
      )
      expect(layer).to.not.be.undefined
    })
  })
})
