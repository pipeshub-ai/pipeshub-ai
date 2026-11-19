import { Router } from 'express';
import { Container } from 'inversify';
import multer from 'multer';

import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { createMulter } from '../../../libs/utils/multer.utils';
import { readStoredAiModelsConfig } from '../../configuration_manager/utils/util';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  createChatCompletion,
  createResponse,
  listModels,
  OpenAICompatDeps,
  retrieveModel,
} from '../controller/openai_compat.controller';
import {
  createFile,
  fileContent,
  listFiles,
  removeFile,
  retrieveFile,
} from '../controller/files.controller';
import {
  listProjects,
  retrieveProject,
} from '../controller/projects.controller';
import {
  cancelFileBatch,
  createFileBatch,
  createVectorStore,
  createVectorStoreFile,
  listFileBatchFiles,
  listVectorStoreFiles,
  listVectorStores,
  modifyVectorStore,
  removeVectorStore,
  removeVectorStoreFile,
  retrieveFileBatch,
  retrieveVectorStore,
  retrieveVectorStoreFile,
} from '../controller/vector-stores.controller';
import {
  cancelFileBatchSchema,
  chatCompletionSchema,
  createFileBatchSchema,
  createFileSchema,
  createVectorStoreFileSchema,
  createVectorStoreSchema,
  modifyVectorStoreSchema,
  responseSchema,
} from '../validators/openai_compat.validators';

// Buffered in memory only to forward it: the collection upload route enforces the platform's own size limit.
const MAX_FILE_BYTES = 100 * 1024 * 1024;

/**
 * OpenAI-compatible surface, mounted at `/api/v1/openai`: a client whose base
 * URL is `{origin}/api/v1/openai` lists the organization's configured chat
 * models and chats through Chat Completions or Responses. Each chat runs
 * PipesHub's assistant (knowledge search, tools, citations) with that model.
 * Files are records, vector stores are collections and projects are projects,
 * each behind the PipesHub route that owns them.
 */
export function createOpenAICompatRouter(container: Container): Router {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const keyValueStoreService = container.get<KeyValueStoreService>(
    'KeyValueStoreService',
  );
  const deps: OpenAICompatDeps = {
    appConfig: container.get<AppConfig>('AppConfig'),
    readModels: () => readStoredAiModelsConfig(keyValueStoreService),
  };
  const authenticate = authMiddleware.authenticate.bind(authMiddleware);
  const guards = [
    authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_CHAT),
  ];
  const kbRead = [authenticate, requireScopes(OAuthScopeNames.KB_READ)];
  const kbUpload = [
    authenticate,
    requireScopes(OAuthScopeNames.KB_UPLOAD, OAuthScopeNames.KB_READ),
  ];
  const kbWrite = [authenticate, requireScopes(OAuthScopeNames.KB_WRITE)];
  const kbDelete = [authenticate, requireScopes(OAuthScopeNames.KB_DELETE)];
  const upload = createMulter({
    storage: multer.memoryStorage(),
    limits: { fileSize: MAX_FILE_BYTES, files: 1 },
  });

  router.get('/models', ...guards, listModels(deps));
  router.get('/models/*', ...guards, retrieveModel(deps));
  router.post(
    '/chat/completions',
    ...guards,
    ValidationMiddleware.validate(chatCompletionSchema),
    createChatCompletion(deps),
  );
  router.post(
    '/responses',
    ...guards,
    ValidationMiddleware.validate(responseSchema),
    createResponse(deps),
  );

  router.get('/files', ...kbRead, listFiles(deps));
  router.post(
    '/files',
    // Creating the files collection on first upload needs kb:write too.
    ...kbUpload,
    requireScopes(OAuthScopeNames.KB_WRITE),
    upload.single('file'),
    ValidationMiddleware.validate(createFileSchema),
    createFile(deps),
  );
  router.get('/files/:fileId', ...kbRead, retrieveFile(deps));
  router.get('/files/:fileId/content', ...kbRead, fileContent(deps));
  router.delete('/files/:fileId', ...kbDelete, removeFile(deps));

  router.get('/vector_stores', ...kbRead, listVectorStores(deps));
  router.post(
    '/vector_stores',
    ...kbUpload,
    requireScopes(OAuthScopeNames.KB_WRITE),
    ValidationMiddleware.validate(createVectorStoreSchema),
    createVectorStore(deps),
  );
  router.get(
    '/vector_stores/:vectorStoreId',
    ...kbRead,
    retrieveVectorStore(deps),
  );
  router.post(
    '/vector_stores/:vectorStoreId',
    ...kbWrite,
    requireScopes(OAuthScopeNames.KB_READ),
    ValidationMiddleware.validate(modifyVectorStoreSchema),
    modifyVectorStore(deps),
  );
  router.delete(
    '/vector_stores/:vectorStoreId',
    ...kbDelete,
    removeVectorStore(deps),
  );
  router.get(
    '/vector_stores/:vectorStoreId/files',
    ...kbRead,
    listVectorStoreFiles(deps),
  );
  router.post(
    '/vector_stores/:vectorStoreId/files',
    ...kbUpload,
    ValidationMiddleware.validate(createVectorStoreFileSchema),
    createVectorStoreFile(deps),
  );
  router.get(
    '/vector_stores/:vectorStoreId/files/:fileId',
    ...kbRead,
    retrieveVectorStoreFile(deps),
  );
  router.delete(
    '/vector_stores/:vectorStoreId/files/:fileId',
    ...kbDelete,
    requireScopes(OAuthScopeNames.KB_READ),
    removeVectorStoreFile(deps),
  );
  router.post(
    '/vector_stores/:vectorStoreId/file_batches',
    ...kbUpload,
    ValidationMiddleware.validate(createFileBatchSchema),
    createFileBatch(deps),
  );
  router.get(
    '/vector_stores/:vectorStoreId/file_batches/:batchId',
    ...kbRead,
    retrieveFileBatch(deps),
  );
  router.post(
    '/vector_stores/:vectorStoreId/file_batches/:batchId/cancel',
    ...kbRead,
    ValidationMiddleware.validate(cancelFileBatchSchema),
    cancelFileBatch(deps),
  );
  router.get(
    '/vector_stores/:vectorStoreId/file_batches/:batchId/files',
    ...kbRead,
    listFileBatchFiles(deps),
  );

  const projectRead = [
    authenticate,
    requireScopes(OAuthScopeNames.PROJECT_READ),
  ];
  router.get('/organization/projects', ...projectRead, listProjects(deps));
  router.get(
    '/organization/projects/:projectId',
    ...projectRead,
    retrieveProject(deps),
  );
  return router;
}
