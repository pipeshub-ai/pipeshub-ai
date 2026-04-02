import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createOrgRouter } from '../../../../src/modules/user_management/routes/org.routes'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { OrgController } from '../../../../src/modules/user_management/controller/org.controller'
import { PrometheusService } from '../../../../src/libs/services/prometheus/prometheus.service'

describe('Org Routes - handler coverage', () => {
  let container: Container
  let mockOrgController: any
  let router: any

  beforeEach(() => {
    container = new Container()

    const mockAuthMiddleware = {
      authenticate: sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      scopedTokenValidator: sinon.stub().returns(
        sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      ),
    }

    mockOrgController = {
      checkOrgExistence: sinon.stub().resolves(),
      createOrg: sinon.stub().resolves(),
      getOrganizationById: sinon.stub().resolves(),
      updateOrganizationDetails: sinon.stub().resolves(),
      deleteOrganization: sinon.stub().resolves(),
      updateOrgLogo: sinon.stub().resolves(),
      removeOrgLogo: sinon.stub().resolves(),
      getOrgLogo: sinon.stub().resolves(),
      getOnboardingStatus: sinon.stub().resolves(),
      updateOnboardingStatus: sinon.stub().resolves(),
    }

    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any)
    container.bind<OrgController>('OrgController').toConstantValue(mockOrgController as any)
    container.bind<any>(PrometheusService).toConstantValue({ recordActivity: sinon.stub() })

    router = createOrgRouter(container)
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

  describe('GET /exists handler', () => {
    it('should call orgController.checkOrgExistence', async () => {
      const handler = findHandler('/exists', 'get')
      expect(handler).to.exist

      const req = {} as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockOrgController.checkOrgExistence.calledOnce).to.be.true
    })

    it('should call next on error', async () => {
      mockOrgController.checkOrgExistence.rejects(new Error('Check failed'))
      const handler = findHandler('/exists', 'get')

      const req = {} as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  describe('POST / handler', () => {
    it('should call orgController.createOrg', async () => {
      const handler = findHandler('/', 'post')
      expect(handler).to.exist

      const req = { body: { accountType: 'individual', contactEmail: 'test@example.com', adminFullName: 'Admin', password: 'pass12345' } } as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockOrgController.createOrg.calledOnce).to.be.true
    })
  })

  describe('GET / handler', () => {
    it('should call orgController.getOrganizationById', async () => {
      const handler = findHandler('/', 'get')
      expect(handler).to.exist

      const req = { user: { orgId: 'org1' } } as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockOrgController.getOrganizationById.calledOnce).to.be.true
    })
  })

  describe('PUT / handler', () => {
    it('should call orgController.updateOrganizationDetails', async () => {
      const handler = findHandler('/', 'put')
      expect(handler).to.exist

      const req = { user: { orgId: 'org1' } } as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockOrgController.updateOrganizationDetails.calledOnce).to.be.true
    })
  })

  describe('DELETE / handler', () => {
    it('should call orgController.deleteOrganization', async () => {
      const handler = findHandler('/', 'delete')
      expect(handler).to.exist

      const req = { user: { orgId: 'org1' } } as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockOrgController.deleteOrganization.calledOnce).to.be.true
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
    })
  })

  describe('GET /onboarding-status handler', () => {
    it('should call orgController.getOnboardingStatus', async () => {
      const handler = findHandler('/onboarding-status', 'get')
      expect(handler).to.exist

      const req = { user: { orgId: 'org1' } } as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockOrgController.getOnboardingStatus.calledOnce).to.be.true
    })
  })

  describe('PUT /onboarding-status handler', () => {
    it('should call orgController.updateOnboardingStatus', async () => {
      const handler = findHandler('/onboarding-status', 'put')
      expect(handler).to.exist

      const req = { body: { status: 'configured' }, user: { orgId: 'org1' } } as any
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockOrgController.updateOnboardingStatus.calledOnce).to.be.true
    })
  })

  describe('route registrations', () => {
    it('should register PUT /logo route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/logo' && l.route.methods.put,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register DELETE /logo route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/logo' && l.route.methods.delete,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register GET /logo route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/logo' && l.route.methods.get,
      )
      expect(layer).to.not.be.undefined
    })
  })
})
