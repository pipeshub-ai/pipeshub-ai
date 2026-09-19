import { Router } from 'express';
import { Container } from 'inversify';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  createProjectSchema,
  listProjectConversationsQuerySchema,
  listProjectsQuerySchema,
  projectIdParamsSchema,
  removeProjectMemberParamsSchema,
  updateProjectSchema,
  upsertProjectMembersSchema,
} from '../validators/project.validators';
import {
  archiveProject,
  createProject,
  deleteProject,
  ensureProjectKnowledgeBase,
  getProjectById,
  getProjectConversations,
  listProjectMembers,
  listProjects,
  pinProject,
  removeProjectMember,
  unarchiveProject,
  unpinProject,
  updateProject,
  upsertProjectMembers,
} from '../controller/project.controller';

export function createProjectsRouter(container: Container): Router {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const appConfig = container.get<AppConfig>('AppConfig');

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
    listProjects(appConfig),
  );

  router.get(
    '/:projectId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_READ),
    ValidationMiddleware.validate(projectIdParamsSchema),
    getProjectById(appConfig),
  );

  router.patch(
    '/:projectId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_WRITE),
    ValidationMiddleware.validate(updateProjectSchema),
    updateProject(appConfig),
  );

  router.delete(
    '/:projectId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_DELETE),
    ValidationMiddleware.validate(projectIdParamsSchema),
    deleteProject(appConfig),
  );

  router.post(
    '/:projectId/archive',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_WRITE),
    ValidationMiddleware.validate(projectIdParamsSchema),
    archiveProject(appConfig),
  );

  router.post(
    '/:projectId/unarchive',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_WRITE),
    ValidationMiddleware.validate(projectIdParamsSchema),
    unarchiveProject(appConfig),
  );

  router.post(
    '/:projectId/pin',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_WRITE),
    ValidationMiddleware.validate(projectIdParamsSchema),
    pinProject(appConfig),
  );

  router.post(
    '/:projectId/unpin',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_WRITE),
    ValidationMiddleware.validate(projectIdParamsSchema),
    unpinProject(appConfig),
  );

  router.get(
    '/:projectId/conversations',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_READ),
    ValidationMiddleware.validate(listProjectConversationsQuerySchema),
    getProjectConversations(appConfig),
  );

  router.get(
    '/:projectId/members',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_READ),
    ValidationMiddleware.validate(projectIdParamsSchema),
    listProjectMembers(appConfig),
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

  router.post(
    '/:projectId/knowledge-base',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.PROJECT_WRITE),
    ValidationMiddleware.validate(projectIdParamsSchema),
    ensureProjectKnowledgeBase(appConfig),
  );

  return router;
}

export default createProjectsRouter;
