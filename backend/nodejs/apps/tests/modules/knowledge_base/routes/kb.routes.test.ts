import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import type { Router } from 'express';
import { createKnowledgeBaseRouter } from '../../../../src/modules/knowledge_base/routes/kb.routes';
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware';
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config';
import { RecordsEventProducer } from '../../../../src/modules/knowledge_base/services/records_events.service';
import { SyncEventProducer } from '../../../../src/modules/knowledge_base/services/sync_events.service';
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service';
import { KB_UPLOAD_LIMITS } from '../../../../src/modules/knowledge_base/constants/kb.constants';
import * as configurationUtil from '../../../../src/modules/configuration_manager/utils/util';
import { FileProcessorService } from '../../../../src/libs/middlewares/file_processor/fp.service';

describe('Knowledge Base Routes', () => {
  let container: Container;
  let router: Router;
  let mockAuthMiddleware: any;
  let mockAppConfig: any;
  let mockRecordsEventProducer: any;
  let mockSyncEventProducer: any;
  let mockKeyValueStore: any;

  function findHandler(path: string, method: string) {
    const layer = router.stack.find(
      (l: any) => l.route && l.route.path === path && l.route.methods[method],
    );
    if (!layer?.route) return null;
    return layer.route.stack[layer.route.stack.length - 1].handle;
  }

  function findRouteStack(path: string, method: string) {
    const layer = router.stack.find(
      (l: any) => l.route && l.route.path === path && l.route.methods[method],
    );
    if (!layer?.route) return [];
    return layer.route.stack.map((stackLayer: any) => stackLayer.handle);
  }

  function getRegisteredRoutes() {
    return router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }));
  }

  function mockRes() {
    return {
      status: sinon.stub().returnsThis(),
      json: sinon.stub().returnsThis(),
      end: sinon.stub().returnsThis(),
    };
  }

  beforeEach(() => {
    container = new Container();

    mockAuthMiddleware = {
      authenticate: (_req: any, _res: any, next: any) => next(),
      scopedTokenValidator: sinon
        .stub()
        .returns((_req: any, _res: any, next: any) => next()),
    };

    mockAppConfig = {
      connectorBackend: 'http://localhost:8088',
      aiBackend: 'http://localhost:8000',
      storage: {
        endpoint: 'http://localhost:3003',
      },
      jwtSecret: 'test-jwt-secret',
      scopedJwtSecret: 'test-scoped-secret',
      cmBackend: 'http://localhost:3001',
    };

    mockRecordsEventProducer = {
      start: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
      stop: sinon.stub().resolves(),
    };

    mockSyncEventProducer = {
      start: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
      stop: sinon.stub().resolves(),
    };

    mockKeyValueStore = {
      get: sinon.stub().resolves(null),
      set: sinon.stub().resolves(),
      delete: sinon.stub().resolves(),
    };


    container
      .bind<AuthMiddleware>('AuthMiddleware')
      .toConstantValue(mockAuthMiddleware as any);
    container
      .bind<AppConfig>('AppConfig')
      .toConstantValue(mockAppConfig as any);
    container
      .bind<RecordsEventProducer>('RecordsEventProducer')
      .toConstantValue(mockRecordsEventProducer);
    container
      .bind<SyncEventProducer>('SyncEventProducer')
      .toConstantValue(mockSyncEventProducer);
    container
      .bind<KeyValueStoreService>('KeyValueStoreService')
      .toConstantValue(mockKeyValueStore);

    router = createKnowledgeBaseRouter(container);
  });

  afterEach(() => {
    sinon.restore();
  });

  it('should return a valid Express router', () => {
    expect(router).to.exist;
    expect(router).to.have.property('stack');
  });

  it('should register knowledge base CRUD routes', () => {
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }));
    const paths = routes.map((r: any) => r.path);

    // KB CRUD - root path / for POST (create) and GET (list)
    const postRoot = routes.find((r: any) => r.path === '/' && r.methods.post);
    expect(postRoot).to.exist;
    const getRoot = routes.find((r: any) => r.path === '/' && r.methods.get);
    expect(getRoot).to.exist;
  });

  it('should register record routes', () => {
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }));
    const paths = routes.map((r: any) => r.path);

    expect(paths).to.include('/record/:recordId');
  });

  it('should register knowledge hub nodes route', () => {
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }));
    const paths = routes.map((r: any) => r.path);

    expect(paths).to.include('/knowledge-hub/nodes');
  });

  it('should register stream record route', () => {
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }));
    const paths = routes.map((r: any) => r.path);

    expect(paths).to.include('/stream/record/:recordId');
  });

  it('should register the unified upload route', () => {
    const paths = getRegisteredRoutes().map((route) => route.path);

    expect(paths).to.include('/:kbId/upload');
    expect(paths).to.not.include('/:kbId/folder/:folderId/upload');
  });

  describe('folder route registration', () => {
    it('should register unified POST /:kbId/folder for root and nested folder creation', () => {
      const routes = getRegisteredRoutes();
      const createFolderRoute = routes.find(
        (route) => route.path === '/:kbId/folder' && route.methods.post,
      );

      expect(createFolderRoute).to.exist;
    });

    it('should wire createFolderSchema validation before the createFolder handler', () => {
      const handlers = findRouteStack('/:kbId/folder', 'post');

      expect(handlers).to.have.lengthOf(4);
      expect(handlers[2]).to.be.a('function');
      expect(handlers[3]).to.be.a('function');
    });

    it('should register folder update and delete routes', () => {
      const routes = getRegisteredRoutes();
      const folderRoutes = routes.filter(
        (route) => route.path === '/:kbId/folder/:folderId',
      );

      expect(folderRoutes.some((route) => route.methods.put)).to.be.true;
      expect(folderRoutes.some((route) => route.methods.delete)).to.be.true;
    });

    it('should not register removed legacy folder and records routes', () => {
      const paths = getRegisteredRoutes().map((route) => route.path);

      expect(paths).to.not.include('/records');
      expect(paths).to.not.include('/:kbId/records');
      expect(paths).to.not.include('/:kbId/children');
      expect(paths).to.not.include('/:kbId/folder/:folderId/subfolder');
      expect(paths).to.not.include('/:kbId/folder/:folderId/children');
    });
  });

  describe('GET /limits', () => {
    it('should register upload limits route', () => {
      const routes = getRegisteredRoutes();
      const limitsRoute = routes.find(
        (route) => route.path === '/limits' && route.methods.get,
      );

      expect(limitsRoute).to.exist;
    });

    it('should return platform max file size when settings resolve', async () => {
      const platformBytes = 64 * 1024 * 1024;
      sinon.stub(configurationUtil, 'getPlatformSettingsFromStore').resolves({
        fileUploadMaxSizeBytes: platformBytes,
        featureFlags: {},
      });

      const handler = findHandler('/limits', 'get');
      const res = mockRes();
      const next = sinon.stub();

      await handler({}, res, next);

      expect(res.status.calledWith(200)).to.be.true;
      expect(res.json.calledOnce).to.be.true;
      expect(res.json.firstCall.args[0]).to.deep.equal({
        maxFilesPerRequest: KB_UPLOAD_LIMITS.maxFilesPerRequest,
        maxFileSizeBytes: platformBytes,
      });
      expect(res.end.calledOnce).to.be.true;
      expect(next.called).to.be.false;
    });

    it('should fall back to default max file size when platform settings fail', async () => {
      sinon
        .stub(configurationUtil, 'getPlatformSettingsFromStore')
        .rejects(new Error('kv unavailable'));

      const handler = findHandler('/limits', 'get');
      const res = mockRes();
      const next = sinon.stub();

      await handler({}, res, next);

      expect(res.status.calledWith(200)).to.be.true;
      expect(res.json.firstCall.args[0]).to.deep.equal({
        maxFilesPerRequest: KB_UPLOAD_LIMITS.maxFilesPerRequest,
        maxFileSizeBytes: KB_UPLOAD_LIMITS.defaultMaxFileSizeBytes,
      });
      expect(next.called).to.be.false;
    });

    it('should forward errors when response methods throw', async () => {
      sinon.stub(configurationUtil, 'getPlatformSettingsFromStore').resolves({
        fileUploadMaxSizeBytes: 1024,
        featureFlags: {},
      });

      const handler = findHandler('/limits', 'get');
      const resError = new Error('response failed');
      const res = {
        status: sinon.stub().throws(resError),
        json: sinon.stub().returnsThis(),
        end: sinon.stub().returnsThis(),
      };
      const next = sinon.stub();

      await handler({}, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0]).to.equal(resError);
    });
  });

  describe('multipart validation middleware', () => {
    it('should call next() for safe multipart form fields', () => {
      const handlers = findRouteStack('/record/:recordId', 'put');
      const validateMultipart = handlers[3];
      const req = {
        body: {
          recordName: 'Quarterly report',
          folderName: 'Engineering Docs',
          kbName: 'Company Knowledge',
        },
      };
      const next = sinon.stub();

      validateMultipart(req, {} as any, next);

      expect(next.calledOnceWithExactly()).to.be.true;
    });

    it('should forward validation errors for unsafe multipart form fields', () => {
      const handlers = findRouteStack('/:kbId/upload', 'post');
      const validateMultipart = handlers[3];
      const req = {
        body: {
          folderName: '<script>alert(1)</script>',
        },
      };
      const next = sinon.stub();

      validateMultipart(req, {} as any, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0]).to.be.instanceOf(Error);
      expect(next.firstCall.args[0].message).to.include(
        'Folder name contains potentially dangerous content',
      );
    });
  });

  describe('dynamic upload middleware', () => {
    it('should process files after successful upload setup', async () => {
      sinon.stub(configurationUtil, 'getPlatformSettingsFromStore').resolves({
        fileUploadMaxSizeBytes: 2048,
        featureFlags: {},
      });

      const processMiddleware = sinon
        .stub()
        .callsFake((_req: any, _res: any, next: any) => next());
      sinon
        .stub(FileProcessorService.prototype, 'upload')
        .returns(((_req: any, _res: any, next: any) => next()) as any);
      sinon
        .stub(FileProcessorService.prototype, 'processFiles')
        .returns(processMiddleware as any);

      const handlers = findRouteStack('/:kbId/upload', 'post');
      const dynamicUpload = handlers[2];
      const req = { headers: {} };
      const res = mockRes();
      const next = sinon.stub();

      await dynamicUpload(req, res, next);

      expect(processMiddleware.calledOnce).to.be.true;
      expect(processMiddleware.firstCall.args[0]).to.equal(req);
      expect(processMiddleware.firstCall.args[1]).to.equal(res);
      expect(processMiddleware.firstCall.args[2]).to.equal(next);
    });

    it('should forward upload callback errors to next()', async () => {
      sinon.stub(configurationUtil, 'getPlatformSettingsFromStore').resolves({
        fileUploadMaxSizeBytes: 2048,
        featureFlags: {},
      });

      const uploadError = new Error('upload failed');
      sinon
        .stub(FileProcessorService.prototype, 'upload')
        .returns(((_req: any, _res: any, next: any) =>
          next(uploadError)) as any);
      const processFilesStub = sinon.stub(
        FileProcessorService.prototype,
        'processFiles',
      );

      const handlers = findRouteStack('/:kbId/upload', 'post');
      const dynamicUpload = handlers[2];
      const next = sinon.stub();

      await dynamicUpload({ headers: {} }, mockRes(), next);

      expect(next.calledOnceWithExactly(uploadError)).to.be.true;
      expect(processFilesStub.called).to.be.false;
    });

    it('should forward setup exceptions to next()', async () => {
      sinon.stub(configurationUtil, 'getPlatformSettingsFromStore').resolves({
        fileUploadMaxSizeBytes: 2048,
        featureFlags: {},
      });

      const setupError = new Error('setup failed');
      sinon.stub(FileProcessorService.prototype, 'upload').throws(setupError);

      const handlers = findRouteStack('/record/:recordId', 'put');
      const dynamicUpload = handlers[2];
      const next = sinon.stub();

      await dynamicUpload({ headers: {} }, mockRes(), next);

      expect(next.calledOnceWithExactly(setupError)).to.be.true;
    });
  });
});
