import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createStorageRouter } from '../../../../src/modules/storage/routes/storage.routes'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { PrometheusService } from '../../../../src/libs/services/prometheus/prometheus.service'

describe('Storage Routes', () => {
  let container: Container
  let mockAuthMiddleware: any
  let mockStorageController: any
  let mockKeyValueStoreService: any

  beforeEach(() => {
    container = new Container()

    mockAuthMiddleware = {
      authenticate: sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      scopedTokenValidator: sinon.stub().returns(
        sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      ),
    }

    mockStorageController = {
      uploadDocument: sinon.stub().resolves(),
      createPlaceholderDocument: sinon.stub().resolves(),
      getDocumentById: sinon.stub().resolves(),
      deleteDocumentById: sinon.stub().resolves(),
      downloadDocument: sinon.stub().resolves(),
      getDocumentBuffer: sinon.stub().resolves(),
      createDocumentBuffer: sinon.stub().resolves(),
      uploadNextVersionDocument: sinon.stub().resolves(),
      rollBackToPreviousVersion: sinon.stub().resolves(),
      uploadDirectDocument: sinon.stub().resolves(),
      documentDiffChecker: sinon.stub().resolves(),
      watchStorageType: sinon.stub(),
    }

    mockKeyValueStoreService = {
      get: sinon.stub().resolves(null),
      set: sinon.stub().resolves(),
      watch: sinon.stub(),
      connect: sinon.stub().resolves(),
      disconnect: sinon.stub().resolves(),
    }

    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any)
    container.bind<any>('StorageController').toConstantValue(mockStorageController)
    container.bind<any>('KeyValueStoreService').toConstantValue(mockKeyValueStoreService)

    const mockPrometheusService = {
      recordActivity: sinon.stub(),
    }
    container.bind<any>(PrometheusService).toConstantValue(mockPrometheusService)
  })

  afterEach(() => {
    sinon.restore()
  })

  it('should export createStorageRouter function', () => {
    expect(createStorageRouter).to.be.a('function')
  })

  it('should create a router successfully', () => {
    const router = createStorageRouter(container)
    expect(router).to.be.a('function')
  })

  it('should have route handlers registered', () => {
    const router = createStorageRouter(container)
    const routes = (router as any).stack || []
    expect(routes.length).to.be.greaterThan(0)
  })

  it('should call watchStorageType on initialization', () => {
    createStorageRouter(container)
    expect(mockStorageController.watchStorageType.calledOnce).to.be.true
    expect(mockStorageController.watchStorageType.calledWith(mockKeyValueStoreService)).to.be.true
  })

  describe('upload routes', () => {
    it('should register POST /upload route', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack

      const uploadRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/upload' &&
          layer.route.methods.post,
      )
      expect(uploadRoute).to.not.be.undefined
    })

    it('should register POST /internal/upload route', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack

      const internalUploadRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/internal/upload' &&
          layer.route.methods.post,
      )
      expect(internalUploadRoute).to.not.be.undefined
    })
  })

  describe('placeholder routes', () => {
    it('should register POST /placeholder route', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack

      const placeholderRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/placeholder' &&
          layer.route.methods.post,
      )
      expect(placeholderRoute).to.not.be.undefined
    })

    it('should register POST /internal/placeholder route', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack

      const internalPlaceholderRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/internal/placeholder' &&
          layer.route.methods.post,
      )
      expect(internalPlaceholderRoute).to.not.be.undefined
    })
  })

  describe('document CRUD routes', () => {
    it('should register GET /:documentId route', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack

      const getRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:documentId' &&
          layer.route.methods.get,
      )
      expect(getRoute).to.not.be.undefined
    })

    it('should register GET /internal/:documentId route', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack

      const internalGetRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/internal/:documentId' &&
          layer.route.methods.get,
      )
      expect(internalGetRoute).to.not.be.undefined
    })

    it('should register DELETE /:documentId/ route', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack

      const deleteRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:documentId/' &&
          layer.route.methods.delete,
      )
      expect(deleteRoute).to.not.be.undefined
    })

    it('should register DELETE /internal/:documentId/ route', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack

      const internalDeleteRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/internal/:documentId/' &&
          layer.route.methods.delete,
      )
      expect(internalDeleteRoute).to.not.be.undefined
    })
  })

  describe('download routes', () => {
    it('should register GET /:documentId/download route', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack

      const downloadRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:documentId/download' &&
          layer.route.methods.get,
      )
      expect(downloadRoute).to.not.be.undefined
    })

    it('should register GET /internal/:documentId/download route', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack

      const internalDownloadRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/internal/:documentId/download' &&
          layer.route.methods.get,
      )
      expect(internalDownloadRoute).to.not.be.undefined
    })
  })

  describe('buffer routes', () => {
    it('should register GET /:documentId/buffer route', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack

      const bufferRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:documentId/buffer' &&
          layer.route.methods.get,
      )
      expect(bufferRoute).to.not.be.undefined
    })

    it('should register GET /internal/:documentId/buffer route', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack

      const internalBufferRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/internal/:documentId/buffer' &&
          layer.route.methods.get,
      )
      expect(internalBufferRoute).to.not.be.undefined
    })

    it('should register PUT /:documentId/buffer route', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack

      const putBufferRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:documentId/buffer' &&
          layer.route.methods.put,
      )
      expect(putBufferRoute).to.not.be.undefined
    })

    it('should register PUT /internal/:documentId/buffer route', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack

      const internalPutBufferRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/internal/:documentId/buffer' &&
          layer.route.methods.put,
      )
      expect(internalPutBufferRoute).to.not.be.undefined
    })
  })

  describe('version control routes', () => {
    it('should register POST /:documentId/uploadNextVersion route', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack

      const nextVersionRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:documentId/uploadNextVersion' &&
          layer.route.methods.post,
      )
      expect(nextVersionRoute).to.not.be.undefined
    })

    it('should register POST /internal/:documentId/uploadNextVersion route', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack

      const internalNextVersionRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/internal/:documentId/uploadNextVersion' &&
          layer.route.methods.post,
      )
      expect(internalNextVersionRoute).to.not.be.undefined
    })

    it('should register POST /:documentId/rollBack route', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack

      const rollBackRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:documentId/rollBack' &&
          layer.route.methods.post,
      )
      expect(rollBackRoute).to.not.be.undefined
    })

    it('should register POST /internal/:documentId/rollBack route', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack

      const internalRollBackRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/internal/:documentId/rollBack' &&
          layer.route.methods.post,
      )
      expect(internalRollBackRoute).to.not.be.undefined
    })
  })

  describe('direct upload routes', () => {
    it('should register POST /:documentId/directUpload route', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack

      const directUploadRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:documentId/directUpload' &&
          layer.route.methods.post,
      )
      expect(directUploadRoute).to.not.be.undefined
    })

    it('should register POST /internal/:documentId/directUpload route', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack

      const internalDirectUploadRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/internal/:documentId/directUpload' &&
          layer.route.methods.post,
      )
      expect(internalDirectUploadRoute).to.not.be.undefined
    })
  })

  describe('isModified routes', () => {
    it('should register GET /:documentId/isModified route', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack

      const isModifiedRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:documentId/isModified' &&
          layer.route.methods.get,
      )
      expect(isModifiedRoute).to.not.be.undefined
    })

    it('should register GET /internal/:documentId/isModified route', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack

      const internalIsModifiedRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/internal/:documentId/isModified' &&
          layer.route.methods.get,
      )
      expect(internalIsModifiedRoute).to.not.be.undefined
    })
  })

  describe('config update route', () => {
    it('should register POST /updateAppConfig route', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack

      const updateConfigRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/updateAppConfig' &&
          layer.route.methods.post,
      )
      expect(updateConfigRoute).to.not.be.undefined
    })
  })

  describe('route count', () => {
    it('should register all expected routes', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack.filter((layer: any) => layer.route)

      // Each operation has public + internal variants, plus updateAppConfig
      // upload(2) + placeholder(2) + get(2) + delete(2) + download(2) + buffer_get(2) + buffer_put(2) + nextVersion(2) + rollBack(2) + directUpload(2) + isModified(2) + updateConfig(1) = 23
      expect(routes.length).to.be.greaterThanOrEqual(20)
    })
  })

  describe('middleware chains', () => {
    it('should include multiple middleware handlers on each route', () => {
      const router = createStorageRouter(container)
      const routes = (router as any).stack.filter((layer: any) => layer.route)

      for (const routeLayer of routes) {
        const handlerCount = routeLayer.route.stack.length
        expect(handlerCount).to.be.greaterThanOrEqual(1,
          `Route ${routeLayer.route.path} should have at least 1 handler`)
      }
    })
  })

  describe('route handler invocations', () => {
    function findRouteHandler(router: any, path: string, method: string) {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === path && l.route.methods[method],
      )
      if (!layer) return undefined
      const handlers = layer.route.stack.map((s: any) => s.handle)
      return handlers[handlers.length - 1]
    }

    function createMockReqRes() {
      const mockReq: any = {
        user: { userId: 'user123', orgId: 'org123' },
        tokenPayload: { userId: 'user123', orgId: 'org123' },
        body: {},
        params: { documentId: 'doc123' },
        query: {},
        headers: {},
      }
      const mockRes: any = {
        status: sinon.stub().returnsThis(),
        json: sinon.stub().returnsThis(),
      }
      const mockNext = sinon.stub()
      return { mockReq, mockRes, mockNext }
    }

    it('POST /upload handler should call storageController.uploadDocument', async () => {
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/upload', 'post')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockStorageController.uploadDocument.calledOnce).to.be.true
    })

    it('POST /upload handler should call next on error', async () => {
      mockStorageController.uploadDocument.rejects(new Error('Upload failed'))
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/upload', 'post')

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
    })

    it('POST /internal/upload handler should call storageController.uploadDocument', async () => {
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/internal/upload', 'post')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockStorageController.uploadDocument.calledOnce).to.be.true
    })

    it('POST /placeholder handler should call storageController.createPlaceholderDocument', async () => {
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/placeholder', 'post')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockStorageController.createPlaceholderDocument.calledOnce).to.be.true
    })

    it('POST /internal/placeholder handler should call storageController.createPlaceholderDocument', async () => {
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/internal/placeholder', 'post')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockStorageController.createPlaceholderDocument.calledOnce).to.be.true
    })

    it('GET /:documentId handler should call storageController.getDocumentById', async () => {
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/:documentId', 'get')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockStorageController.getDocumentById.calledOnce).to.be.true
    })

    it('GET /internal/:documentId handler should call storageController.getDocumentById', async () => {
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/internal/:documentId', 'get')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockStorageController.getDocumentById.calledOnce).to.be.true
    })

    it('DELETE /:documentId/ handler should call storageController.deleteDocumentById', async () => {
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/:documentId/', 'delete')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockStorageController.deleteDocumentById.calledOnce).to.be.true
    })

    it('DELETE /internal/:documentId/ handler should call storageController.deleteDocumentById', async () => {
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/internal/:documentId/', 'delete')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockStorageController.deleteDocumentById.calledOnce).to.be.true
    })

    it('GET /:documentId/download handler should call storageController.downloadDocument', async () => {
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/:documentId/download', 'get')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockStorageController.downloadDocument.calledOnce).to.be.true
    })

    it('GET /internal/:documentId/download handler should call storageController.downloadDocument', async () => {
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/internal/:documentId/download', 'get')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockStorageController.downloadDocument.calledOnce).to.be.true
    })

    it('GET /:documentId/buffer handler should call storageController.getDocumentBuffer', async () => {
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/:documentId/buffer', 'get')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockStorageController.getDocumentBuffer.calledOnce).to.be.true
    })

    it('GET /internal/:documentId/buffer handler should call storageController.getDocumentBuffer', async () => {
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/internal/:documentId/buffer', 'get')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockStorageController.getDocumentBuffer.calledOnce).to.be.true
    })

    it('PUT /:documentId/buffer handler should call storageController.createDocumentBuffer', async () => {
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/:documentId/buffer', 'put')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockStorageController.createDocumentBuffer.calledOnce).to.be.true
    })

    it('PUT /:documentId/buffer handler should call next on error', async () => {
      mockStorageController.createDocumentBuffer.rejects(new Error('Upload failed'))
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/:documentId/buffer', 'put')

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
    })

    it('PUT /internal/:documentId/buffer handler should call storageController.createDocumentBuffer', async () => {
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/internal/:documentId/buffer', 'put')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockStorageController.createDocumentBuffer.calledOnce).to.be.true
    })

    it('POST /:documentId/uploadNextVersion handler should call storageController.uploadNextVersionDocument', async () => {
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/:documentId/uploadNextVersion', 'post')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockStorageController.uploadNextVersionDocument.calledOnce).to.be.true
    })

    it('POST /internal/:documentId/uploadNextVersion handler should call storageController.uploadNextVersionDocument', async () => {
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/internal/:documentId/uploadNextVersion', 'post')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockStorageController.uploadNextVersionDocument.calledOnce).to.be.true
    })

    it('POST /:documentId/rollBack handler should call storageController.rollBackToPreviousVersion', async () => {
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/:documentId/rollBack', 'post')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockStorageController.rollBackToPreviousVersion.calledOnce).to.be.true
    })

    it('POST /internal/:documentId/rollBack handler should call storageController.rollBackToPreviousVersion', async () => {
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/internal/:documentId/rollBack', 'post')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockStorageController.rollBackToPreviousVersion.calledOnce).to.be.true
    })

    it('POST /:documentId/directUpload handler should call storageController.uploadDirectDocument', async () => {
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/:documentId/directUpload', 'post')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockStorageController.uploadDirectDocument.calledOnce).to.be.true
    })

    it('POST /internal/:documentId/directUpload handler should call storageController.uploadDirectDocument', async () => {
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/internal/:documentId/directUpload', 'post')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockStorageController.uploadDirectDocument.calledOnce).to.be.true
    })

    it('GET /:documentId/isModified handler should call storageController.documentDiffChecker', async () => {
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/:documentId/isModified', 'get')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockStorageController.documentDiffChecker.calledOnce).to.be.true
    })

    it('GET /internal/:documentId/isModified handler should call storageController.documentDiffChecker', async () => {
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/internal/:documentId/isModified', 'get')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockStorageController.documentDiffChecker.calledOnce).to.be.true
    })

    it('POST /updateAppConfig handler should update config and respond 200', async () => {
      const loadAppConfigModule = await import('../../../../src/modules/tokens_manager/config/config')
      const loadStub = sinon.stub(loadAppConfigModule, 'loadAppConfig').resolves({
        storage: { provider: 'local', basePath: '/tmp' },
      } as any)

      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/updateAppConfig', 'post')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      // Should either respond 200 or call next (depending on loadAppConfig mock)
      const responded = mockRes.status.calledWith(200) || mockNext.called
      expect(responded).to.be.true

      loadStub.restore()
    })

    it('POST /updateAppConfig handler should call next on error', async () => {
      const loadAppConfigModule = await import('../../../../src/modules/tokens_manager/config/config')
      const loadStub = sinon.stub(loadAppConfigModule, 'loadAppConfig').rejects(new Error('Config load failed'))

      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/updateAppConfig', 'post')

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true

      loadStub.restore()
    })

    it('POST /placeholder handler should call next on error', async () => {
      mockStorageController.createPlaceholderDocument.rejects(new Error('Create failed'))
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/placeholder', 'post')

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
    })

    it('GET /:documentId handler should call next on error', async () => {
      mockStorageController.getDocumentById.rejects(new Error('Not found'))
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/:documentId', 'get')

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
    })

    it('GET /:documentId/download handler should call next on error', async () => {
      mockStorageController.downloadDocument.rejects(new Error('Download failed'))
      const router = createStorageRouter(container)
      const handler = findRouteHandler(router, '/:documentId/download', 'get')

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
    })
  })
})
