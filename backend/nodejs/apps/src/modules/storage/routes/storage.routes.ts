import { Router, Response, NextFunction } from 'express';
import { Container } from 'inversify';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import {
  AuthenticatedUserRequest,
  AuthenticatedServiceRequest,
} from '../../../libs/middlewares/types';
import {
  getIndexableMimeTypes,
  getMimeType,
  indexableExtensions,
} from '../mimetypes/mimetypes';
import { Logger } from '../../../libs/services/logger.service';
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';
import {
  UploadNewSchema,
  DocumentIdParams,
  GetBufferSchema,
  CreateDocumentSchema,
  UploadNextVersionSchema,
  RollBackToPreviousVersionSchema,
  DirectUploadSchema,
  DocumentIdParamsWithVersion,
} from '../validators/validators';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { FileProcessorFactory } from '../../../libs/middlewares/file_processor/fp.factory';
import { FileProcessorService } from '../../../libs/middlewares/file_processor/fp.service';
import { FileProcessingType } from '../../../libs/middlewares/file_processor/fp.constant';
import { getPlatformSettingsFromStore } from '../../configuration_manager/utils/util';
import { KB_UPLOAD_LIMITS } from '../../knowledge_base/constants/kb.constants';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { StorageController } from '../controllers/storage.controller';
import { AppConfig, loadAppConfig } from '../../tokens_manager/config/config';
import { DefaultStorageConfig } from '../../tokens_manager/services/cm.service';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';

const logger = Logger.getInstance({ service: 'StorageRoutes' });
const INDEXABLE_MIME_TYPES = getIndexableMimeTypes();

export function createStorageRouter(container: Container): Router {
  const router = Router();
  const keyValueStoreService = container.get<KeyValueStoreService>(
    'KeyValueStoreService',
  );
  let storageController = container.get<StorageController>('StorageController');
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  storageController.watchStorageType(keyValueStoreService);

  // Resolve the platform-configured per-file upload cap so internal routes
  // honour the same limit as the user-facing KB upload route.
  const resolveMaxUploadSize = async (): Promise<number> => {
    try {
      const settings = await getPlatformSettingsFromStore(keyValueStoreService);
      return settings.fileUploadMaxSizeBytes;
    } catch {
      return KB_UPLOAD_LIMITS.defaultMaxFileSizeBytes;
    }
  };

  router.post(
    '/upload',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_UPLOAD),
    metricsMiddleware(container),
    ...FileProcessorFactory.createBufferUploadProcessor({
      fieldName: 'file',
      allowedMimeTypes: INDEXABLE_MIME_TYPES,
      allowedExtensions: [...indexableExtensions],
      resolveMimeType: (ext: string) => getMimeType(ext),
      maxFilesAllowed: 1,
      isMultipleFilesAllowed: false,
      processingType: FileProcessingType.BUFFER,
      maxFileSize: 1024 * 1024 * 1000,
      strictFileUpload: true,
    }).getMiddleware,
    ValidationMiddleware.validate(UploadNewSchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        await storageController.uploadDocument(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.post(
    '/internal/upload',
    authMiddleware.scopedTokenValidator(TokenScopes.STORAGE_TOKEN),
    metricsMiddleware(container),
    async (req: AuthenticatedServiceRequest, res: Response, next: NextFunction) => {
      try {
        const maxFileSize = await resolveMaxUploadSize();
        const service = new FileProcessorService({
          fieldName: 'file',
          allowedMimeTypes: INDEXABLE_MIME_TYPES,
          allowedExtensions: [...indexableExtensions],
          resolveMimeType: (ext: string) => getMimeType(ext),
          maxFilesAllowed: 1,
          isMultipleFilesAllowed: false,
          processingType: FileProcessingType.BUFFER,
          maxFileSize,
          strictFileUpload: true,
        });
        const upload = service.upload();
        upload(req, res, (err: any) => {
          if (err) return next(err);
          service.processFiles()(req, res, next);
        });
      } catch (error) {
        next(error);
      }
    },
    ValidationMiddleware.validate(UploadNewSchema),
    async (
      req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        await storageController.uploadDocument(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  // Create a document placeholder and then client can upload the
  // document to the placeholder documentPath via direct upload api
  // provided by storage vendors

  router.post(
    '/placeholder',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(CreateDocumentSchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        return await storageController.createPlaceholderDocument(
          req,
          res,
          next,
        );
      } catch (error) {
        next(error);
      }
    },
  );

  router.post(
    '/internal/placeholder',
    authMiddleware.scopedTokenValidator(TokenScopes.STORAGE_TOKEN),
    metricsMiddleware(container),
    ValidationMiddleware.validate(CreateDocumentSchema),
    async (
      req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        return await storageController.createPlaceholderDocument(
          req,
          res,
          next,
        );
      } catch (error) {
        next(error);
      }
    },
  );
  router.get(
    '/:documentId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(DocumentIdParams),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        return await storageController.getDocumentById(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );
  router.get(
    '/internal/:documentId',
    authMiddleware.scopedTokenValidator(TokenScopes.STORAGE_TOKEN),
    metricsMiddleware(container),
    ValidationMiddleware.validate(DocumentIdParams),
    async (
      req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        return await storageController.getDocumentById(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.delete(
    '/:documentId/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_DELETE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(DocumentIdParams),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        return await storageController.deleteDocumentById(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );
  router.delete(
    '/internal/:documentId/',
    authMiddleware.scopedTokenValidator(TokenScopes.STORAGE_TOKEN),
    metricsMiddleware(container),
    ValidationMiddleware.validate(DocumentIdParams),
    async (
      req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        return await storageController.deleteDocumentById(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/:documentId/download',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(DocumentIdParamsWithVersion),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        return await storageController.downloadDocument(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );
  router.get(
    '/internal/:documentId/download',
    authMiddleware.scopedTokenValidator(TokenScopes.STORAGE_TOKEN),
    metricsMiddleware(container),
    ValidationMiddleware.validate(DocumentIdParamsWithVersion),
    async (
      req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        return await storageController.downloadDocument(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/:documentId/buffer',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(GetBufferSchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        return await storageController.getDocumentBuffer(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  // Document Operations Routes
  router.get(
    '/internal/:documentId/buffer',
    authMiddleware.scopedTokenValidator(TokenScopes.STORAGE_TOKEN),
    metricsMiddleware(container),
    ValidationMiddleware.validate(GetBufferSchema),
    async (
      req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        return await storageController.getDocumentBuffer(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.put(
    '/:documentId/buffer',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_WRITE),
    metricsMiddleware(container),
    ...FileProcessorFactory.createBufferUploadProcessor({
      fieldName: 'file',
      allowedMimeTypes: INDEXABLE_MIME_TYPES,
      allowedExtensions: [...indexableExtensions],
      resolveMimeType: (ext: string) => getMimeType(ext),
      maxFilesAllowed: 1,
      isMultipleFilesAllowed: false,
      processingType: FileProcessingType.BUFFER,
      maxFileSize: 1024 * 1024 * 100,
      strictFileUpload: true,
    }).getMiddleware,
    ValidationMiddleware.validate(DocumentIdParams),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        return await storageController.createDocumentBuffer(req, res, next);
      } catch (error: any) {
        logger.error(`Failed to upload buffer: ${error.message}`);
        next(error);
      }
    },
  );

  router.put(
    '/internal/:documentId/buffer',
    authMiddleware.scopedTokenValidator(TokenScopes.STORAGE_TOKEN),
    metricsMiddleware(container),
    ...FileProcessorFactory.createBufferUploadProcessor({
      fieldName: 'file',
      allowedMimeTypes: INDEXABLE_MIME_TYPES,
      allowedExtensions: [...indexableExtensions],
      resolveMimeType: (ext: string) => getMimeType(ext),
      maxFilesAllowed: 1,
      isMultipleFilesAllowed: false,
      processingType: FileProcessingType.BUFFER,
      maxFileSize: 1024 * 1024 * 100,
      strictFileUpload: true,
    }).getMiddleware,
    ValidationMiddleware.validate(DocumentIdParams),
    async (
      req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        return await storageController.createDocumentBuffer(req, res, next);
      } catch (error: any) {
        logger.error(`Failed to upload buffer: ${error.message}`);
        next(error);
      }
    },
  );
  router.post(
    '/:documentId/uploadNextVersion',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_WRITE),
    ...FileProcessorFactory.createBufferUploadProcessor({
      fieldName: 'file',
      allowedMimeTypes: INDEXABLE_MIME_TYPES,
      allowedExtensions: [...indexableExtensions],
      resolveMimeType: (ext: string) => getMimeType(ext),
      maxFilesAllowed: 1,
      isMultipleFilesAllowed: false,
      processingType: FileProcessingType.BUFFER,
      maxFileSize: 1024 * 1024 * 100,
      strictFileUpload: true,
    }).getMiddleware,
    metricsMiddleware(container),
    ValidationMiddleware.validate(UploadNextVersionSchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        return await storageController.uploadNextVersionDocument(
          req,
          res,
          next,
        );
      } catch (error) {
        next(error);
      }
    },
  );
  // Version Control Routes
  router.post(
    '/internal/:documentId/uploadNextVersion',
    authMiddleware.scopedTokenValidator(TokenScopes.STORAGE_TOKEN),
    async (req: AuthenticatedServiceRequest, res: Response, next: NextFunction) => {
      try {
        const maxFileSize = await resolveMaxUploadSize();
        const service = new FileProcessorService({
          fieldName: 'file',
          allowedMimeTypes: INDEXABLE_MIME_TYPES,
          allowedExtensions: [...indexableExtensions],
          resolveMimeType: (ext: string) => getMimeType(ext),
          maxFilesAllowed: 1,
          isMultipleFilesAllowed: false,
          processingType: FileProcessingType.BUFFER,
          maxFileSize,
          strictFileUpload: true,
        });
        const upload = service.upload();
        upload(req, res, (err: any) => {
          if (err) return next(err);
          service.processFiles()(req, res, next);
        });
      } catch (error) {
        next(error);
      }
    },
    metricsMiddleware(container),
    ValidationMiddleware.validate(UploadNextVersionSchema),
    async (
      req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        return await storageController.uploadNextVersionDocument(
          req,
          res,
          next,
        );
      } catch (error) {
        next(error);
      }
    },
  );
  router.post(
    '/:documentId/rollBack',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(RollBackToPreviousVersionSchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        return await storageController.rollBackToPreviousVersion(
          req,
          res,
          next,
        );
      } catch (error) {
        next(error);
      }
    },
  );

  // Rollback to previous version
  router.post(
    '/internal/:documentId/rollBack',
    authMiddleware.scopedTokenValidator(TokenScopes.STORAGE_TOKEN),
    metricsMiddleware(container),
    ValidationMiddleware.validate(RollBackToPreviousVersionSchema),
    async (
      req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        return await storageController.rollBackToPreviousVersion(
          req,
          res,
          next,
        );
      } catch (error) {
        next(error);
      }
    },
  );

  router.post(
    '/:documentId/directUpload',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(DirectUploadSchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        return await storageController.uploadDirectDocument(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.post(
    '/internal/:documentId/directUpload',
    authMiddleware.scopedTokenValidator(TokenScopes.STORAGE_TOKEN),
    metricsMiddleware(container),
    ValidationMiddleware.validate(DirectUploadSchema),
    async (
      req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        return await storageController.uploadDirectDocument(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/:documentId/isModified',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(DocumentIdParams),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        return await storageController.documentDiffChecker(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/internal/:documentId/isModified',
    authMiddleware.scopedTokenValidator(TokenScopes.STORAGE_TOKEN),
    metricsMiddleware(container),
    ValidationMiddleware.validate(DocumentIdParams),
    async (
      req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        return await storageController.documentDiffChecker(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );
  router.post(
    '/updateAppConfig',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    async (
      _req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const updatedConfig: AppConfig = await loadAppConfig();
        const storageConfig = updatedConfig.storage;

        container
          .rebind<DefaultStorageConfig>('StorageConfig')
          .toDynamicValue(() => storageConfig);

        container
          .rebind<StorageController>('StorageController')
          .toDynamicValue(() => {
            return new StorageController(
              storageConfig,
              logger,
              keyValueStoreService,
            );
          });
        res.status(200).json({
          message: 'Storage configuration updated successfully',
          config: updatedConfig,
        });
        return;
      } catch (error) {
        next(error);
      }
    },
  );

  return router;
}
