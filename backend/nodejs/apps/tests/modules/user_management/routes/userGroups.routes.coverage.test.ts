import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createUserGroupRouter } from '../../../../src/modules/user_management/routes/userGroups.routes'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { UserGroupController } from '../../../../src/modules/user_management/controller/userGroups.controller'

describe('UserGroups Routes - handler coverage', () => {
  let container: Container
  let mockUserGroupController: any
  let router: any

  beforeEach(() => {
    container = new Container()

    const mockAuthMiddleware = {
      authenticate: sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
    }

    mockUserGroupController = {
      createUserGroup: sinon.stub().resolves(),
      getAllUserGroups: sinon.stub().resolves(),
      getUserGroupById: sinon.stub().resolves(),
      updateGroup: sinon.stub().resolves(),
      deleteGroup: sinon.stub().resolves(),
      addUsersToGroups: sinon.stub().resolves(),
      removeUsersFromGroups: sinon.stub().resolves(),
      getUsersInGroup: sinon.stub().resolves(),
      getGroupsForUser: sinon.stub().resolves(),
      getGroupStatistics: sinon.stub().resolves(),
    }

    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any)
    container.bind<UserGroupController>('UserGroupController').toConstantValue(mockUserGroupController as any)

    router = createUserGroupRouter(container)
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
    return { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis(), send: sinon.stub().returnsThis() }
  }

  describe('POST / handler', () => {
    it('should call userGroupController.createUserGroup', async () => {
      const handler = findHandler('/', 'post')
      expect(handler).to.exist
      await handler({} as any, mockRes(), sinon.stub())
      expect(mockUserGroupController.createUserGroup.calledOnce).to.be.true
    })

    it('should call next on error', async () => {
      mockUserGroupController.createUserGroup.rejects(new Error('Create failed'))
      const handler = findHandler('/', 'post')
      const next = sinon.stub()
      await handler({} as any, mockRes(), next)
      expect(next.calledOnce).to.be.true
    })
  })

  describe('GET / handler', () => {
    it('should call userGroupController.getAllUserGroups', async () => {
      const handler = findHandler('/', 'get')
      expect(handler).to.exist
      await handler({} as any, mockRes(), sinon.stub())
      expect(mockUserGroupController.getAllUserGroups.calledOnce).to.be.true
    })
  })

  describe('GET /:groupId handler', () => {
    it('should call userGroupController.getUserGroupById', async () => {
      const handler = findHandler('/:groupId', 'get')
      expect(handler).to.exist
      await handler({ params: { groupId: '507f1f77bcf86cd799439011' } } as any, mockRes(), sinon.stub())
      expect(mockUserGroupController.getUserGroupById.calledOnce).to.be.true
    })
  })

  describe('PUT /:groupId handler', () => {
    it('should call userGroupController.updateGroup', async () => {
      const handler = findHandler('/:groupId', 'put')
      expect(handler).to.exist
      await handler({ params: { groupId: '507f1f77bcf86cd799439011' } } as any, mockRes(), sinon.stub())
      expect(mockUserGroupController.updateGroup.calledOnce).to.be.true
    })
  })

  describe('DELETE /:groupId handler', () => {
    it('should call userGroupController.deleteGroup', async () => {
      const handler = findHandler('/:groupId', 'delete')
      expect(handler).to.exist
      await handler({ params: { groupId: '507f1f77bcf86cd799439011' } } as any, mockRes(), sinon.stub())
      expect(mockUserGroupController.deleteGroup.calledOnce).to.be.true
    })
  })

  describe('POST /add-users handler', () => {
    it('should call userGroupController.addUsersToGroups', async () => {
      const handler = findHandler('/add-users', 'post')
      expect(handler).to.exist
      await handler({} as any, mockRes(), sinon.stub())
      expect(mockUserGroupController.addUsersToGroups.calledOnce).to.be.true
    })
  })

  describe('POST /remove-users handler', () => {
    it('should call userGroupController.removeUsersFromGroups', async () => {
      const handler = findHandler('/remove-users', 'post')
      expect(handler).to.exist
      await handler({} as any, mockRes(), sinon.stub())
      expect(mockUserGroupController.removeUsersFromGroups.calledOnce).to.be.true
    })
  })

  describe('GET /:groupId/users handler', () => {
    it('should call userGroupController.getUsersInGroup', async () => {
      const handler = findHandler('/:groupId/users', 'get')
      expect(handler).to.exist
      await handler({ params: { groupId: '507f1f77bcf86cd799439011' } } as any, mockRes(), sinon.stub())
      expect(mockUserGroupController.getUsersInGroup.calledOnce).to.be.true
    })
  })

  describe('GET /users/:userId handler', () => {
    it('should call userGroupController.getGroupsForUser', async () => {
      const handler = findHandler('/users/:userId', 'get')
      expect(handler).to.exist
      await handler({ params: { userId: 'user1' } } as any, mockRes(), sinon.stub())
      expect(mockUserGroupController.getGroupsForUser.calledOnce).to.be.true
    })
  })

  describe('GET /stats/list handler', () => {
    it('should call userGroupController.getGroupStatistics', async () => {
      const handler = findHandler('/stats/list', 'get')
      expect(handler).to.exist
      await handler({} as any, mockRes(), sinon.stub())
      expect(mockUserGroupController.getGroupStatistics.calledOnce).to.be.true
    })
  })

  describe('GET /health handler', () => {
    it('should return health status', () => {
      const handler = findHandler('/health', 'get')
      expect(handler).to.exist
      const res = mockRes() as any
      handler({} as any, res)
      expect(res.json.calledOnce).to.be.true
      expect(res.json.firstCall.args[0].status).to.equal('healthy')
    })
  })
})
