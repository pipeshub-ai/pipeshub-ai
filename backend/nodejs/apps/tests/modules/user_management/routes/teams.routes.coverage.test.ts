import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createTeamsRouter } from '../../../../src/modules/user_management/routes/teams.routes'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { TeamsController } from '../../../../src/modules/user_management/controller/teams.controller'
import { PrometheusService } from '../../../../src/libs/services/prometheus/prometheus.service'

describe('Teams Routes - handler coverage', () => {
  let container: Container
  let mockTeamsController: any
  let router: any

  beforeEach(() => {
    container = new Container()

    const mockAuthMiddleware = {
      authenticate: sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
    }

    mockTeamsController = {
      createTeam: sinon.stub().resolves(),
      listTeams: sinon.stub().resolves(),
      getTeam: sinon.stub().resolves(),
      updateTeam: sinon.stub().resolves(),
      deleteTeam: sinon.stub().resolves(),
      getTeamUsers: sinon.stub().resolves(),
      addUsersToTeam: sinon.stub().resolves(),
      removeUserFromTeam: sinon.stub().resolves(),
      updateTeamUsersPermissions: sinon.stub().resolves(),
      getUserTeams: sinon.stub().resolves(),
      getUserCreatedTeams: sinon.stub().resolves(),
    }

    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any)
    container.bind<TeamsController>('TeamsController').toConstantValue(mockTeamsController as any)
    container.bind<any>(PrometheusService).toConstantValue({ recordActivity: sinon.stub() })

    router = createTeamsRouter(container)
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
    it('should call teamsController.createTeam', async () => {
      const handler = findHandler('/', 'post')
      expect(handler).to.exist
      await handler({ body: { name: 'Team1' } } as any, mockRes(), sinon.stub())
      expect(mockTeamsController.createTeam.calledOnce).to.be.true
    })

    it('should call next on error', async () => {
      mockTeamsController.createTeam.rejects(new Error('Create failed'))
      const handler = findHandler('/', 'post')
      const next = sinon.stub()
      await handler({} as any, mockRes(), next)
      expect(next.calledOnce).to.be.true
    })
  })

  describe('GET / handler', () => {
    it('should call teamsController.listTeams', async () => {
      const handler = findHandler('/', 'get')
      expect(handler).to.exist
      await handler({ query: {} } as any, mockRes(), sinon.stub())
      expect(mockTeamsController.listTeams.calledOnce).to.be.true
    })
  })

  describe('GET /:teamId handler', () => {
    it('should call teamsController.getTeam', async () => {
      const handler = findHandler('/:teamId', 'get')
      expect(handler).to.exist
      await handler({ params: { teamId: 'team1' } } as any, mockRes(), sinon.stub())
      expect(mockTeamsController.getTeam.calledOnce).to.be.true
    })
  })

  describe('PUT /:teamId handler', () => {
    it('should call teamsController.updateTeam', async () => {
      const handler = findHandler('/:teamId', 'put')
      expect(handler).to.exist
      await handler({ params: { teamId: 'team1' }, body: {} } as any, mockRes(), sinon.stub())
      expect(mockTeamsController.updateTeam.calledOnce).to.be.true
    })
  })

  describe('DELETE /:teamId handler', () => {
    it('should call teamsController.deleteTeam', async () => {
      const handler = findHandler('/:teamId', 'delete')
      expect(handler).to.exist
      await handler({ params: { teamId: 'team1' } } as any, mockRes(), sinon.stub())
      expect(mockTeamsController.deleteTeam.calledOnce).to.be.true
    })
  })

  describe('GET /:teamId/users handler', () => {
    it('should call teamsController.getTeamUsers', async () => {
      const handler = findHandler('/:teamId/users', 'get')
      expect(handler).to.exist
      await handler({ params: { teamId: 'team1' } } as any, mockRes(), sinon.stub())
      expect(mockTeamsController.getTeamUsers.calledOnce).to.be.true
    })
  })

  describe('POST /:teamId/users handler', () => {
    it('should call teamsController.addUsersToTeam', async () => {
      const handler = findHandler('/:teamId/users', 'post')
      expect(handler).to.exist
      await handler({ params: { teamId: 'team1' }, body: { userIds: ['u1'] } } as any, mockRes(), sinon.stub())
      expect(mockTeamsController.addUsersToTeam.calledOnce).to.be.true
    })
  })

  describe('DELETE /:teamId/users handler', () => {
    it('should call teamsController.removeUserFromTeam', async () => {
      const handler = findHandler('/:teamId/users', 'delete')
      expect(handler).to.exist
      await handler({ params: { teamId: 'team1' }, body: { userIds: ['u1'] } } as any, mockRes(), sinon.stub())
      expect(mockTeamsController.removeUserFromTeam.calledOnce).to.be.true
    })
  })

  describe('PUT /:teamId/users/permissions handler', () => {
    it('should call teamsController.updateTeamUsersPermissions', async () => {
      const handler = findHandler('/:teamId/users/permissions', 'put')
      expect(handler).to.exist
      await handler({ params: { teamId: 'team1' }, body: {} } as any, mockRes(), sinon.stub())
      expect(mockTeamsController.updateTeamUsersPermissions.calledOnce).to.be.true
    })
  })

  describe('GET /user/teams handler', () => {
    it('should call teamsController.getUserTeams', async () => {
      const handler = findHandler('/user/teams', 'get')
      expect(handler).to.exist
      await handler({} as any, mockRes(), sinon.stub())
      expect(mockTeamsController.getUserTeams.calledOnce).to.be.true
    })
  })

  describe('GET /user/teams/created handler', () => {
    it('should call teamsController.getUserCreatedTeams', async () => {
      const handler = findHandler('/user/teams/created', 'get')
      expect(handler).to.exist
      await handler({ query: {} } as any, mockRes(), sinon.stub())
      expect(mockTeamsController.getUserCreatedTeams.calledOnce).to.be.true
    })
  })
})
