import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createCrawlingManagerRouter } from '../../../../src/modules/crawling_manager/routes/cm_routes'
import { CrawlingSchedulerService } from '../../../../src/modules/crawling_manager/services/crawling_service'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config'
import { PrometheusService } from '../../../../src/libs/services/prometheus/prometheus.service'

describe('Crawling Manager Routes', () => {
  let container: Container
  let mockAuthMiddleware: any
  let mockCrawlingService: any
  let mockAppConfig: any

  beforeEach(() => {
    container = new Container()

    mockAuthMiddleware = {
      authenticate: (_req: any, _res: any, next: any) => next(),
      scopedTokenValidator: sinon.stub().returns((_req: any, _res: any, next: any) => next()),
    }

    mockCrawlingService = {
      scheduleJob: sinon.stub().resolves({ id: 'job-1' }),
      getJobStatus: sinon.stub().resolves(null),
      removeJob: sinon.stub().resolves(),
      getAllJobs: sinon.stub().resolves([]),
      removeAllJobs: sinon.stub().resolves(),
      pauseJob: sinon.stub().resolves(),
      resumeJob: sinon.stub().resolves(),
      getQueueStats: sinon.stub().resolves({}),
    }

    mockAppConfig = {
      connectorBackend: 'http://localhost:8088',
    }

    const mockPrometheusService = {
      recordActivity: sinon.stub(),
      getMetrics: sinon.stub().resolves(''),
    }

    container.bind<CrawlingSchedulerService>(CrawlingSchedulerService).toConstantValue(mockCrawlingService)
    container.bind<AuthMiddleware>(AuthMiddleware).toConstantValue(mockAuthMiddleware as any)
    container.bind<AppConfig>('AppConfig').toConstantValue(mockAppConfig as any)
    container.bind(PrometheusService).toConstantValue(mockPrometheusService as any)
  })

  afterEach(() => {
    sinon.restore()
  })

  it('should return a valid Express router', () => {
    const router = createCrawlingManagerRouter(container)
    expect(router).to.exist
    expect(router).to.have.property('stack')
  })

  it('should register schedule route (POST)', () => {
    const router = createCrawlingManagerRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const scheduleRoute = routes.find(
      (r: any) => r.path === '/:connector/:connectorId/schedule' && r.methods.post,
    )
    expect(scheduleRoute).to.exist
  })

  it('should register schedule status route (GET)', () => {
    const router = createCrawlingManagerRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const statusRoute = routes.find(
      (r: any) => r.path === '/:connector/:connectorId/schedule' && r.methods.get,
    )
    expect(statusRoute).to.exist
  })

  it('should register get all schedules route (GET)', () => {
    const router = createCrawlingManagerRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const allStatusRoute = routes.find(
      (r: any) => r.path === '/schedule/all' && r.methods.get,
    )
    expect(allStatusRoute).to.exist
  })

  it('should register delete all schedules route (DELETE)', () => {
    const router = createCrawlingManagerRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const deleteAllRoute = routes.find(
      (r: any) => r.path === '/schedule/all' && r.methods.delete,
    )
    expect(deleteAllRoute).to.exist
  })

  it('should register remove specific job route (DELETE)', () => {
    const router = createCrawlingManagerRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const removeRoute = routes.find(
      (r: any) => r.path === '/:connector/:connectorId/remove' && r.methods.delete,
    )
    expect(removeRoute).to.exist
  })

  it('should register pause job route (POST)', () => {
    const router = createCrawlingManagerRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const pauseRoute = routes.find(
      (r: any) => r.path === '/:connector/:connectorId/pause' && r.methods.post,
    )
    expect(pauseRoute).to.exist
  })

  it('should register resume job route (POST)', () => {
    const router = createCrawlingManagerRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const resumeRoute = routes.find(
      (r: any) => r.path === '/:connector/:connectorId/resume' && r.methods.post,
    )
    expect(resumeRoute).to.exist
  })

  it('should register queue stats route (GET)', () => {
    const router = createCrawlingManagerRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const statsRoute = routes.find(
      (r: any) => r.path === '/stats' && r.methods.get,
    )
    expect(statsRoute).to.exist
  })

  it('should register exactly 8 routes', () => {
    const router = createCrawlingManagerRouter(container)
    const routes = router.stack.filter((layer: any) => layer.route)
    expect(routes).to.have.length(8)
  })
})
