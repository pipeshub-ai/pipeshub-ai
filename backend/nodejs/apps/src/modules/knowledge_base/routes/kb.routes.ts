import { Router } from 'express';
import { Container } from 'inversify';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import {
  deleteRecord,
  getRecordById,
  updateRecord,
  getRecordBuffer,
  reindexRecord,
  reindexRecordGroup,
  createKnowledgeBase,
  listKnowledgeBases,
  getKnowledgeBase,
  updateKnowledgeBase,
  deleteKnowledgeBase,
  updateKBPermission,
  removeKBPermission,
  createKBPermission,
  listKBPermissions,
  updateFolder,
  deleteFolder,
  uploadRecords,
  createFolder,
  getKnowledgeHubNodes,
  moveRecord,
} from '../controllers/kb_controllers';
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import {
  getRecordByIdSchema,
  updateRecordSchema,
  deleteRecordSchema,
  reindexRecordGroupSchema,
  createKBSchema,
  getKBSchema,
  updateKBSchema,
  deleteKBSchema,
  createFolderSchema,
  kbPermissionSchema,
  getPermissionsSchema,
  updatePermissionsSchema,
  deletePermissionsSchema,
  updateFolderSchema,
  deleteFolderSchema,
  uploadRecordsSchema,
  listKnowledgeBasesSchema,
  reindexRecordSchema,
  moveRecordSchema,
} from '../validators/validators';
// Clean up unused commented import
import { FileProcessingType } from '../../../libs/middlewares/file_processor/fp.constant';
import { extensionToMimeType, getMimeType } from '../../storage/mimetypes/mimetypes';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { FileProcessorService } from '../../../libs/middlewares/file_processor/fp.service';
import { KB_UPLOAD_LIMITS } from '../constants/kb.constants';
import { getPlatformSettingsFromStore } from '../../configuration_manager/utils/util';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { RequestHandler, Response, NextFunction } from 'express';
import { Logger } from '../../../libs/services/logger.service';
import { validateNoXSS, validateNoFormatSpecifiers } from '../../../utils/xss-sanitization';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';

const logger = Logger.getInstance({
  service: 'KnowledgeBaseRoutes',
});

export function createKnowledgeBaseRouter(
  container: Container,
): Router {
  const router = Router();
  const appConfig = container.get<AppConfig>('AppConfig');
  const keyValueStoreService = container.get<KeyValueStoreService>(
    'KeyValueStoreService',
  );
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');

  // Helper: resolve current max upload size (bytes) from platform settings
  const resolveMaxUploadSize = async (): Promise<number> => {
    try {
      const settings = await getPlatformSettingsFromStore(keyValueStoreService);
      return settings.fileUploadMaxSizeBytes;
    } catch (_e) {
      // Fallback to default if utility fails
      return KB_UPLOAD_LIMITS.defaultMaxFileSizeBytes;
    }
  };

  // Middleware to validate multipart form data fields for XSS
  // This runs after multer processes the multipart data
  const validateMultipartFormData: RequestHandler = (
    req: AuthenticatedUserRequest,
    _res: Response,
    next: NextFunction,
  ) => {
    try {
      // Validate recordName if present in body
      if (req.body.recordName && typeof req.body.recordName === 'string') {
        validateNoXSS(req.body.recordName, 'Record name');
        validateNoFormatSpecifiers(req.body.recordName, 'Record name');
      }

      // Validate folderName if present in body
      if (req.body.folderName && typeof req.body.folderName === 'string') {
        validateNoXSS(req.body.folderName, 'Folder name');
        validateNoFormatSpecifiers(req.body.folderName, 'Folder name');
      }

      // Validate kbName if present in body
      if (req.body.kbName && typeof req.body.kbName === 'string') {
        validateNoXSS(req.body.kbName, 'Knowledge base name');
        validateNoFormatSpecifiers(req.body.kbName, 'Knowledge base name');
      }

      next();
    } catch (error) {
      next(error);
    }
  };

  // Create per-request dynamic buffer upload processor
  const createDynamicBufferUpload = (opts: {
    fieldName: string;
    allowedMimeTypes: string[];
    maxFilesAllowed: number;
    isMultipleFilesAllowed: boolean;
    strictFileUpload: boolean;
    allowedExtensions?: string[];
    partialUpload?: boolean;
  }): RequestHandler[] => {
    const handler: RequestHandler = async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const maxFileSize = await resolveMaxUploadSize();
        const service = new FileProcessorService({
          fieldName: opts.fieldName,
          allowedMimeTypes: opts.allowedMimeTypes,
          maxFilesAllowed: opts.maxFilesAllowed,
          isMultipleFilesAllowed: opts.isMultipleFilesAllowed,
          processingType: FileProcessingType.BUFFER,
          maxFileSize,
          strictFileUpload: opts.strictFileUpload,
          allowedExtensions: opts.allowedExtensions,
          partialUpload: opts.partialUpload,
          resolveMimeType: (ext: string) => getMimeType(ext),
        });
        const upload = service.upload();
        upload(req, res, (err: any) => {
          if (err) return next(err);
          const process = service.processFiles();
          process(req, res, next);
        });
      } catch (_e) {
        logger.error('Error creating dynamic buffer upload', { error: _e });
        next(_e);
      }
    };
    return [handler];
  };

  // create knowledge base
  router.post(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(createKBSchema),
    createKnowledgeBase(appConfig),
  );

  // get all knowledge base
  router.get(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(listKnowledgeBasesSchema),
    listKnowledgeBases(appConfig),
  );

  // Knowledge Hub unified browse API - Root
  router.get(
    '/knowledge-hub/nodes',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_READ),
    metricsMiddleware(container),
    getKnowledgeHubNodes(appConfig),
  );

  // Knowledge Hub unified browse API - Children
  router.get(
    '/knowledge-hub/nodes/:parentType/:parentId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_READ),
    metricsMiddleware(container),
    getKnowledgeHubNodes(appConfig),
  );

  // Get a specific record by ID
  router.get(
    '/record/:recordId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(getRecordByIdSchema),
    getRecordById(appConfig),
  );

  // Update a record
  router.put(
    '/record/:recordId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_WRITE),
    metricsMiddleware(container),
    ...createDynamicBufferUpload({
      fieldName: 'file',
      allowedMimeTypes: Object.values(extensionToMimeType),
      maxFilesAllowed: 1,
      isMultipleFilesAllowed: false,
      strictFileUpload: false,
    }),
    // Validate multipart form data after file processing
    validateMultipartFormData,
    ValidationMiddleware.validate(updateRecordSchema),
    updateRecord(keyValueStoreService, appConfig),
  );

  // Delete a record by ID (old one also deletes connector record one issue connector record relations not deleted)
  router.delete(
    '/record/:recordId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_DELETE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(deleteRecordSchema),
    deleteRecord(appConfig),
  );

  // Old api for streaming records
  router.get(
    '/stream/record/:recordId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(getRecordByIdSchema),
    getRecordBuffer(appConfig.connectorBackend),
  );

  // reindex a record
  router.post(
    '/reindex/record/:recordId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(reindexRecordSchema),
    reindexRecord(appConfig),
  );

  // reindex a record group
  router.post(
    '/reindex/record-group/:recordGroupId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(reindexRecordGroupSchema),
    reindexRecordGroup(appConfig),
  );

  // Limits endpoint for clients to discover constraints
  router.get(
    '/limits',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_READ),
    metricsMiddleware(container),
    async (
      _req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        res
          .status(200)
          .json({
            maxFilesPerRequest: KB_UPLOAD_LIMITS.maxFilesPerRequest,
            maxFileSizeBytes: await resolveMaxUploadSize(),
          })
          .end();
      } catch (_e) {
        logger.error('Error getting limits', { error: _e });
        next(_e);
      }
    },
  );

  // get specific knowledge base
  router.get(
    '/:kbId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(getKBSchema),
    getKnowledgeBase(appConfig),
  );

  // update specific knowledge base
  router.put(
    '/:kbId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateKBSchema),
    updateKnowledgeBase(appConfig),
  );

  // delete specific knowledge base
  router.delete(
    '/:kbId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_DELETE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(deleteKBSchema),
    deleteKnowledgeBase(appConfig),
  );

  // Upload records to KB root or folder (?folderId= optional)
  router.post(
    '/:kbId/upload',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_UPLOAD),
    metricsMiddleware(container),
    // File processing middleware (dynamic max size)
    ...createDynamicBufferUpload({
      fieldName: 'files',
      allowedMimeTypes: Object.values(extensionToMimeType),
      allowedExtensions: Object.keys(extensionToMimeType).map((e) =>
        e.toLowerCase(),
      ),
      maxFilesAllowed: KB_UPLOAD_LIMITS.maxFilesPerRequest,
      isMultipleFilesAllowed: true,
      strictFileUpload: true,
      // Reject oversize / unsupported files individually instead of failing the
      // whole batch; rejected files are reported back as per-file failures.
      partialUpload: true,
    }),
    // Validate multipart form data after file processing
    validateMultipartFormData,
    // Validation middleware
    ValidationMiddleware.validate(uploadRecordsSchema),

    // Upload handler
    uploadRecords(keyValueStoreService, appConfig),
  );

  // Create folder (root or nested via optional folderId query param)
  router.post(
    '/:kbId/folder',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(createFolderSchema),
    createFolder(appConfig),
  );

  // update folder
  router.put(
    '/:kbId/folder/:folderId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateFolderSchema),
    updateFolder(appConfig),
  );

  // delete folder
  router.delete(
    '/:kbId/folder/:folderId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_DELETE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(deleteFolderSchema),
    deleteFolder(appConfig),
  );

  // Create permission
  router.post(
    '/:kbId/permissions',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(kbPermissionSchema),
    createKBPermission(appConfig),
  );

  // Get all permissions for KB
  router.get(
    '/:kbId/permissions',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(getPermissionsSchema),
    listKBPermissions(appConfig),
  );

  // Update permission
  router.put(
    '/:kbId/permissions',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(updatePermissionsSchema),
    updateKBPermission(appConfig),
  );

  router.delete(
    '/:kbId/permissions',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_DELETE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(deletePermissionsSchema),
    removeKBPermission(appConfig),
  );

  // Move record (file or folder) to another location
  router.put(
    '/:kbId/record/:recordId/move',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(moveRecordSchema),
    moveRecord(appConfig),
  );

  return router;
}
