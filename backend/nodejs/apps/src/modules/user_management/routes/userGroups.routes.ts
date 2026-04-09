import { Router, Request, Response, NextFunction } from 'express';
import { Container } from 'inversify';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { userAdminCheck } from '../middlewares/userAdminCheck';
import { UserGroupController } from '../controller/userGroups.controller';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import {
  AddUsersToGroupsValidationSchema,
  GroupValidationSchema,
  IdValidationSchema,
  RemoveUsersFromGroupsValidationSchema,
} from '../validation/userGroup.schemas';

export function createUserGroupRouter(container: Container) {
  const router = Router();

  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');

  router.post(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USERGROUP_WRITE),
    ValidationMiddleware.validate(GroupValidationSchema),
    userAdminCheck,
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const userGroupController = container.get<UserGroupController>(
          'UserGroupController',
        );
        await userGroupController.createUserGroup(req, res);
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USERGROUP_READ),
    userAdminCheck,
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const userGroupController = container.get<UserGroupController>(
          'UserGroupController',
        );
        await userGroupController.getAllUserGroups(req, res);
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/:groupId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USERGROUP_READ),
    ValidationMiddleware.validate(IdValidationSchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userGroupController = container.get<UserGroupController>(
          'UserGroupController',
        );
        await userGroupController.getUserGroupById(req, res);
      } catch (error) {
        next(error);
      }
    },
  );

  router.put(
    '/:groupId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USERGROUP_WRITE),
    userAdminCheck,
    ValidationMiddleware.validate(IdValidationSchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userGroupController = container.get<UserGroupController>(
          'UserGroupController',
        );
        await userGroupController.updateGroup(req, res);
      } catch (error) {
        next(error);
      }
    },
  );

  router.delete(
    '/:groupId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USERGROUP_WRITE),
    userAdminCheck,
    ValidationMiddleware.validate(IdValidationSchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userGroupController = container.get<UserGroupController>(
          'UserGroupController',
        );
        await userGroupController.deleteGroup(req, res);
      } catch (error) {
        next(error);
      }
    },
  );

  router.post(
    '/add-users',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USERGROUP_WRITE),
    ValidationMiddleware.validate(AddUsersToGroupsValidationSchema),
    userAdminCheck,
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userGroupController = container.get<UserGroupController>(
          'UserGroupController',
        );
        await userGroupController.addUsersToGroups(req, res);
      } catch (error) {
        next(error);
      }
    },
  );

  router.post(
    '/remove-users',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USERGROUP_WRITE),
    ValidationMiddleware.validate(RemoveUsersFromGroupsValidationSchema),
    userAdminCheck,
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userGroupController = container.get<UserGroupController>(
          'UserGroupController',
        );
        await userGroupController.removeUsersFromGroups(req, res);
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/:groupId/users',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USERGROUP_READ),
    ValidationMiddleware.validate(IdValidationSchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userGroupController = container.get<UserGroupController>(
          'UserGroupController',
        );
        await userGroupController.getUsersInGroup(req, res);
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/users/:userId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USERGROUP_READ),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userGroupController = container.get<UserGroupController>(
          'UserGroupController',
        );
        await userGroupController.getGroupsForUser(req, res);
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/stats/list',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USERGROUP_READ),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userGroupController = container.get<UserGroupController>(
          'UserGroupController',
        );
        await userGroupController.getGroupStatistics(req, res);
      } catch (error) {
        next(error);
      }
    },
  );

  // Health check endpoint
  router.get('/health', (_req: Request, res: Response) => {
    res.json({
      status: 'healthy',
      timestamp: new Date().toISOString(),
    });
  });

  return router;
}
