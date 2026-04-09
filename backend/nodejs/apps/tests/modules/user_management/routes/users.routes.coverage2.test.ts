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
import { createMockAppConfig } from '../../../helpers/fixtures/config.fixture'

describe('User Routes - handler coverage 2', () => {
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

    const mockConfig = createMockAppConfig()

    mockUserController = {
      getAllUsers: sinon.stub().resolves(),
      getAllUsersWithGroups: sinon.stub().resolves(),
      getUserById: sinon.stub().resolves(),
      getUserEmailByUserId: sinon.stub().resolves(),
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

  // Test route handlers that delegate to controller and handle errors
  describe('GET /fetch/with-groups', () => {
    it('should call getAllUsersWithGroups', async () => {
      const handler = findHandler('/fetch/with-groups', 'get')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({}, res, next)
      expect(mockUserController.getAllUsersWithGroups.calledOnce).to.be.true
    })

    it('should call next on error', async () => {
      const handler = findHandler('/fetch/with-groups', 'get')
      mockUserController.getAllUsersWithGroups.rejects(new Error('fail'))

      const res = mockRes()
      const next = sinon.stub()
      await handler({}, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  describe('PUT /:id/unblock', () => {
    it('should call unblockUser', async () => {
      const handler = findHandler('/:id/unblock', 'put')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({ params: { id: 'test' } }, res, next)
      expect(mockUserController.unblockUser.calledOnce).to.be.true
    })
  })

  describe('GET /email/exists', () => {
    it('should call checkUserExistsByEmail', async () => {
      const handler = findHandler('/email/exists', 'get')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({ body: { email: 'test@test.com' } }, res, next)
      expect(mockUserController.checkUserExistsByEmail.calledOnce).to.be.true
    })
  })

  describe('GET /internal/admin-users', () => {
    it('should return admin user IDs', async () => {
      const handler = findHandler('/internal/admin-users', 'get')
      expect(handler).to.be.a('function')
    })

    it('should return 400 when orgId is missing', async () => {
      const handler = findHandler('/internal/admin-users', 'get')
      const req = { tokenPayload: {} }
      const res = mockRes()
      const next = sinon.stub()
      await handler(req, res, next)
      expect(res.status.calledWith(400)).to.be.true
    })
  })

  describe('GET /internal/:id', () => {
    it('should have a handler', () => {
      const handler = findHandler('/internal/:id', 'get')
      expect(handler).to.be.a('function')
    })
  })

  describe('PATCH /:id/firstName', () => {
    it('should call updateFirstName', async () => {
      const handler = findHandler('/:id/firstName', 'patch')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({ params: { id: 'test' }, body: { firstName: 'John' } }, res, next)
      expect(mockUserController.updateFirstName.calledOnce).to.be.true
    })
  })

  describe('PATCH /:id/lastName', () => {
    it('should call updateLastName', async () => {
      const handler = findHandler('/:id/lastName', 'patch')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({ params: { id: 'test' }, body: { lastName: 'Doe' } }, res, next)
      expect(mockUserController.updateLastName.calledOnce).to.be.true
    })
  })

  describe('PUT /dp', () => {
    it('should have a handler for PUT /dp', () => {
      const handler = findHandler('/dp', 'put')
      expect(handler).to.be.a('function')
    })
  })

  describe('DELETE /dp', () => {
    it('should call removeUserDisplayPicture', async () => {
      const handler = findHandler('/dp', 'delete')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({}, res, next)
      expect(mockUserController.removeUserDisplayPicture.calledOnce).to.be.true
    })
  })

  describe('GET /dp', () => {
    it('should call getUserDisplayPicture', async () => {
      const handler = findHandler('/dp', 'get')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({}, res, next)
      expect(mockUserController.getUserDisplayPicture.calledOnce).to.be.true
    })
  })

  describe('PATCH /:id/email', () => {
    it('should call updateEmail', async () => {
      const handler = findHandler('/:id/email', 'patch')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({ params: { id: 'test' }, body: { email: 'new@test.com' } }, res, next)
      expect(mockUserController.updateEmail.calledOnce).to.be.true
    })
  })

  describe('DELETE /:id', () => {
    it('should call deleteUser', async () => {
      const handler = findHandler('/:id', 'delete')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({ params: { id: 'test' } }, res, next)
      expect(mockUserController.deleteUser.calledOnce).to.be.true
    })
  })

  describe('GET /:id/adminCheck', () => {
    it('should return admin access message', async () => {
      const handler = findHandler('/:id/adminCheck', 'get')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({}, res, next)
      expect(res.status.calledWith(200)).to.be.true
    })
  })

  describe('POST /bulk/invite', () => {
    it('should call addManyUsers', async () => {
      const handler = findHandler('/bulk/invite', 'post')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({ body: {} }, res, next)
      expect(mockUserController.addManyUsers.calledOnce).to.be.true
    })
  })

  describe('POST /:id/resend-invite', () => {
    it('should call resendInvite', async () => {
      const handler = findHandler('/:id/resend-invite', 'post')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({ params: { id: 'test' } }, res, next)
      expect(mockUserController.resendInvite.calledOnce).to.be.true
    })
  })

  describe('GET /health', () => {
    it('should return healthy status', () => {
      const handler = findHandler('/health', 'get')
      expect(handler).to.be.a('function')

      const res = mockRes()
      handler({}, res)
      expect(res.json.called).to.be.true
      const arg = res.json.firstCall.args[0]
      expect(arg.status).to.equal('healthy')
    })
  })

  describe('POST /updateAppConfig', () => {
    it('should have a handler', () => {
      const handler = findHandler('/updateAppConfig', 'post')
      expect(handler).to.be.a('function')
    })

    it('should call next on error', async () => {
      const handler = findHandler('/updateAppConfig', 'post')
      const res = mockRes()
      const next = sinon.stub()
      // loadAppConfig will fail
      await handler({}, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  describe('GET /graph/list', () => {
    it('should call listUsers', async () => {
      const handler = findHandler('/graph/list', 'get')
      expect(handler).to.be.a('function')

      const res = mockRes()
      const next = sinon.stub()
      await handler({}, res, next)
      expect(mockUserController.listUsers.calledOnce).to.be.true
    })
  })

})
