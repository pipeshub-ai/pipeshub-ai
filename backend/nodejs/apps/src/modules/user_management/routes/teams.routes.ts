import { Router, Request, Response, NextFunction } from 'express';
import { Container } from 'inversify';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';

import { TeamsController } from '../controller/teams.controller';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import {
  createTeamSchema,
  deleteTeamSchema,
  getTeamSchema,
  getTeamUsersSchema,
  getUserTeamsQuerySchema,
  updateTeamSchema,
} from '../validators/teams.validators';

export function createTeamsRouter(container: Container) {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');

  router.post(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.TEAM_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(createTeamSchema),
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const teamsController =
          container.get<TeamsController>('TeamsController');
        await teamsController.createTeam(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/:teamId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.TEAM_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(getTeamSchema),
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const teamsController = container.get<TeamsController>('TeamsController');
        await teamsController.getTeam(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.put(
    '/:teamId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.TEAM_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateTeamSchema),
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const teamsController = container.get<TeamsController>('TeamsController');
        await teamsController.updateTeam(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.delete(
    '/:teamId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.TEAM_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(deleteTeamSchema),
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const teamsController = container.get<TeamsController>('TeamsController');
        await teamsController.deleteTeam(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/:teamId/users',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.TEAM_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(getTeamUsersSchema),
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const teamsController = container.get<TeamsController>('TeamsController');
        await teamsController.getTeamUsers(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/user/teams',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.TEAM_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(getUserTeamsQuerySchema),
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const teamsController = container.get<TeamsController>('TeamsController');
        await teamsController.getUserTeams(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  return router;
}
