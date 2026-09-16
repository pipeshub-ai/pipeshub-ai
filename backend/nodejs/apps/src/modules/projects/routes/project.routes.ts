import { Router, Response, NextFunction, RequestHandler } from 'express';
import { Container } from 'inversify';
import multer from 'multer';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { AppConfig } from '../../tokens_manager/config/config';
import type { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { getPlatformSettingsFromStore } from '../../configuration_manager/utils/util';
import { KB_UPLOAD_LIMITS } from '../../knowledge_base/constants/kb.constants';
import {
  createProjectSchema,
  listProjectConversationsQuerySchema,
  listProjectsQuerySchema,
  projectIdParamsSchema,
  removeProjectFileParamsSchema,
  removeProjectMemberParamsSchema,
  updateProjectSchema,
  upsertProjectMembersSchema,
} from '../validators/project.validators';
import {
  archiveProject,
  createProject,
  deleteProject,
  deleteProjectFile,
  getProjectById,
  getProjectConversations,
  listProjectMembers,
  listProjects,
  pinProject,
  removeProjectMember,
  unarchiveProject,
  unpinProject,
  updateProject,
  uploadProjectFiles,
  upsertProjectMembers,
} from '../controller/project.controller';

const PROJECT_FILE_MAX_COUNT = 10;

export function createProjectsRouter(container: Container): Router {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const appConfig = container.get<AppConfig>('AppConfig');

  const resolveMaxUploadSize = async (): Promise<number> => {
    try {
      const kvs = container.get<KeyValueStoreService>('KeyValueStoreService');
      const settings = await getPlatformSettingsFromStore(kvs);
      return settings.fileUploadMaxSizeBytes;
    } catch {
      return KB_UPLOAD_LIMITS.defaultMaxFileSizeBytes;
    }
  };

  const projectFileUploadMiddleware: RequestHandler = async (
    req: AuthenticatedUserRequest,
    _res: Response,
    next: NextFunction,
  ) => {
    try {
      const maxFileSize = await resolveMaxUploadSize();
      const upload = multer({
        storage: multer.memoryStorage(),
        limits: { fileSize: maxFileSize, files: PROJECT_FILE_MAX_COUNT },
      });
      upload.array('files')(req, _res, next);
    } catch (error) {
      next(error);
    }
  };

  router.post(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_WRITE),
    ValidationMiddleware.validate(createProjectSchema),
    createProject,
  );

  router.get(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_READ),
    ValidationMiddleware.validate(listProjectsQuerySchema),
    listProjects,
  );

  router.get(
    '/:projectId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_READ),
    ValidationMiddleware.validate(projectIdParamsSchema),
    getProjectById,
  );

  router.patch(
    '/:projectId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_WRITE),
    ValidationMiddleware.validate(updateProjectSchema),
    updateProject,
  );

  router.delete(
    '/:projectId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_DELETE),
    ValidationMiddleware.validate(projectIdParamsSchema),
    deleteProject,
  );

  router.post(
    '/:projectId/archive',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_WRITE),
    ValidationMiddleware.validate(projectIdParamsSchema),
    archiveProject,
  );

  router.post(
    '/:projectId/unarchive',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_WRITE),
    ValidationMiddleware.validate(projectIdParamsSchema),
    unarchiveProject,
  );

  router.post(
    '/:projectId/pin',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_WRITE),
    ValidationMiddleware.validate(projectIdParamsSchema),
    pinProject,
  );

  router.post(
    '/:projectId/unpin',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_WRITE),
    ValidationMiddleware.validate(projectIdParamsSchema),
    unpinProject,
  );

  router.get(
    '/:projectId/conversations',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_READ),
    ValidationMiddleware.validate(listProjectConversationsQuerySchema),
    getProjectConversations,
  );

  router.post(
    '/:projectId/files',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_WRITE),
    projectFileUploadMiddleware,
    ValidationMiddleware.validate(projectIdParamsSchema),
    uploadProjectFiles(appConfig),
  );

  router.delete(
    '/:projectId/files/:recordId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_WRITE),
    ValidationMiddleware.validate(removeProjectFileParamsSchema),
    deleteProjectFile(appConfig),
  );

  router.get(
    '/:projectId/members',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_READ),
    ValidationMiddleware.validate(projectIdParamsSchema),
    listProjectMembers,
  );

  router.put(
    '/:projectId/members',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_WRITE),
    ValidationMiddleware.validate(upsertProjectMembersSchema),
    upsertProjectMembers(appConfig),
  );

  router.delete(
    '/:projectId/members/:memberUserId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_WRITE),
    ValidationMiddleware.validate(removeProjectMemberParamsSchema),
    removeProjectMember(appConfig),
  );

  return router;
}

export default createProjectsRouter;
