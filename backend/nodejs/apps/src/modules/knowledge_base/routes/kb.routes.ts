import { Router } from 'express';
import { Container } from 'inversify';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import {
  deleteRecord,
  getRecordById,
  updateRecord,
  getRecordBuffer,
  reindexRecord,
  getConnectorStats,
  reindexAllRecords,
  resyncConnectorRecords,
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
  getKBContent,
  getFolderContents,
  getAllRecords,
  uploadRecordsToFolder,
  createNestedFolder,
  createRootFolder,
  uploadRecordsToKB,
} from '../controllers/kb_controllers';
import { ArangoService } from '../../../libs/services/arango.service';
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import {
  getRecordByIdSchema,
  updateRecordSchema,
  deleteRecordSchema,
  reindexAllRecordSchema,
  resyncConnectorSchema,
  createKBSchema,
  getKBSchema,
  updateKBSchema,
  deleteKBSchema,
  createFolderSchema,
  kbPermissionSchema,
  getFolderSchema,
  getPermissionsSchema,
  updatePermissionsSchema,
  deletePermissionsSchema,
  updateFolderSchema,
  deleteFolderSchema,
  getAllRecordsSchema,
  getAllKBRecordsSchema,
  uploadRecordsSchema,
  uploadRecordsToFolderSchema,
  listKnowledgeBasesSchema,
  reindexRecordSchema,
  getConnectorStatsSchema,
} from '../validators/validators';
import { FileProcessorFactory } from '../../../libs/middlewares/file_processor/fp.factory';
import { FileProcessingType } from '../../../libs/middlewares/file_processor/fp.constant';
import { extensionToMimeType } from '../../storage/mimetypes/mimetypes';
import { RecordRelationService } from '../services/kb.relation.service';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { RecordsEventProducer } from '../services/records_events.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { SyncEventProducer } from '../services/sync_events.service';
import { userAdminCheck } from '../../user_management/middlewares/userAdminCheck';

export function createKnowledgeBaseRouter(container: Container): Router {
  const router = Router();
  const appConfig = container.get<AppConfig>('AppConfig');
  const arangoService = container.get<ArangoService>('ArangoService');
  const recordsEventProducer = container.get<RecordsEventProducer>(
    'RecordsEventProducer',
  );
  const syncEventProducer =
    container.get<SyncEventProducer>('SyncEventProducer');

  const recordRelationService = new RecordRelationService(
    arangoService,
    recordsEventProducer,
    syncEventProducer,
    appConfig.storage,
  );
  const keyValueStoreService = container.get<KeyValueStoreService>(
    'KeyValueStoreService',
  );
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');

  // create knowledge base
  router.post(
    '/',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(createKBSchema),
    createKnowledgeBase(appConfig),
  );

  // get all knowledge base
  router.get(
    '/',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(listKnowledgeBasesSchema),
    listKnowledgeBases(appConfig),
  );

  // Get all records (new)
  router.get(
    '/records',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(getAllRecordsSchema),
    getAllRecords(appConfig),
  );

  // Get a specific record by ID
  router.get(
    '/record/:recordId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(getRecordByIdSchema),
    getRecordById(appConfig),
  );

  // Update a record
  router.put(
    '/record/:recordId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ...FileProcessorFactory.createBufferUploadProcessor({
      fieldName: 'file',
      allowedMimeTypes: Object.values(extensionToMimeType),
      maxFilesAllowed: 1,
      isMultipleFilesAllowed: false,
      processingType: FileProcessingType.BUFFER,
      maxFileSize: 1024 * 1024 * 30,
      strictFileUpload: false,
    }).getMiddleware,
    ValidationMiddleware.validate(updateRecordSchema),
    updateRecord(keyValueStoreService, appConfig),
  );

  // Delete a record by ID (old one also deletes connector record one issue connector record relations not deleted)
  router.delete(
    '/record/:recordId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(deleteRecordSchema),
    deleteRecord(appConfig),
  );

  // Old api for streaming records 
  router.get(
    '/stream/record/:recordId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(getRecordByIdSchema),
    getRecordBuffer(appConfig.connectorBackend),
  );

  // reindex a record
  router.post(
    '/reindex/record/:recordId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(reindexRecordSchema),
    reindexRecord(appConfig),
  );

  // connector stats
  router.get(
    '/stats/:connector',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    userAdminCheck,
    ValidationMiddleware.validate(getConnectorStatsSchema),
    getConnectorStats(appConfig),
  );

  // reindex all failed records per connector
  router.post(
    '/reindex-all/connector',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    userAdminCheck,
    ValidationMiddleware.validate(reindexAllRecordSchema),
    reindexAllRecords(recordRelationService),
  );

  // resync connector records
  router.post(
    '/resync/connector',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    userAdminCheck,
    ValidationMiddleware.validate(resyncConnectorSchema),
    resyncConnectorRecords(recordRelationService),
  );

  // get specific knowledge base
  router.get(
    '/:kbId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(getKBSchema),
    getKnowledgeBase(appConfig),
  );

  // update specific knowledge base
  router.put(
    '/:kbId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateKBSchema),
    updateKnowledgeBase(appConfig),
  );

  // delete specific knowledge base
  router.delete(
    '/:kbId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(deleteKBSchema),
    deleteKnowledgeBase(appConfig),
  );

  // Get records for a specific KB
  router.get(
    '/:kbId/records',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(getAllKBRecordsSchema),
    getKBContent(appConfig),
  );

  // upload folder in the kb along with the direct record creation in the kb
  router.post(
    '/:kbId/upload',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    // File processing middleware
    ...FileProcessorFactory.createBufferUploadProcessor({
      fieldName: 'files',
      allowedMimeTypes: Object.values(extensionToMimeType),
      maxFilesAllowed: 1000, // Allow more files for folder uploads
      isMultipleFilesAllowed: true,
      processingType: FileProcessingType.BUFFER,
      maxFileSize: 1024 * 1024 * 30, // 30MB per file
      strictFileUpload: true,
    }).getMiddleware,

    // Validation middleware
    ValidationMiddleware.validate(uploadRecordsSchema),

    // Upload handler
    uploadRecordsToKB(recordRelationService, keyValueStoreService, appConfig),
  );

  // Upload records to a specific folder in the KB
  router.post(
    '/:kbId/folder/:folderId/upload',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    // File processing middleware
    ...FileProcessorFactory.createBufferUploadProcessor({
      fieldName: 'files',
      allowedMimeTypes: Object.values(extensionToMimeType),
      maxFilesAllowed: 1000, // Allow more files for folder uploads
      isMultipleFilesAllowed: true,
      processingType: FileProcessingType.BUFFER,
      maxFileSize: 1024 * 1024 * 30, // 30MB per file
      strictFileUpload: true,
    }).getMiddleware,

    // Validation middleware
    ValidationMiddleware.validate(uploadRecordsToFolderSchema),

    // Upload handler
    uploadRecordsToFolder(
      recordRelationService,
      keyValueStoreService,
      appConfig,
    ),
  );

  // Create a root folder
  router.post(
    '/:kbId/folder',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(createFolderSchema),
    createRootFolder(appConfig),
  );

  // create a nested subfolder
  router.post(
    '/:kbId/folder/:folderId/subfolder',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(createFolderSchema),
    createNestedFolder(appConfig),
  );

  // Get folder contents
  router.get(
    '/:kbId/folder/:folderId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(getFolderSchema),
    getFolderContents(appConfig),
  );

  // update folder
  router.put(
    '/:kbId/folder/:folderId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateFolderSchema),
    updateFolder(appConfig),
  );

  // delete folder
  router.delete(
    '/:kbId/folder/:folderId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(deleteFolderSchema),
    deleteFolder(appConfig),
  );

  // Create permission
  router.post(
    '/:kbId/permissions',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(kbPermissionSchema),
    createKBPermission(appConfig),
  );

  // Get all permissions for KB
  router.get(
    '/:kbId/permissions',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(getPermissionsSchema),
    listKBPermissions(appConfig),
  );

  // Update permission
  router.put(
    '/:kbId/permissions',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(updatePermissionsSchema),
    updateKBPermission(appConfig),
  );

  router.delete(
    '/:kbId/permissions',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(deletePermissionsSchema),
    removeKBPermission(appConfig),
  );

  return router;
}