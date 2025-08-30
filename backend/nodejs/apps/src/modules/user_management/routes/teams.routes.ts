import { Router, Request, Response, NextFunction } from 'express';
import { z } from 'zod';
import { Container } from 'inversify';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';

import { TeamsController } from '../controller/teams.controller';

const createTeamValidationSchema = z.object({
  body: z.object({
    name: z.string().min(1, 'Name is required'),
    description: z.string().optional(),
    userIds: z.array(z.string()).optional(),
    role: z.string().optional(),
  }),
});

const listTeamsValidationSchema = z.object({
  query: z.object({
    search: z.string().optional(),
    limit: z
      .preprocess((arg) => Number(arg), z.number().min(1).max(100).default(10))
      .optional(),
    page: z
      .preprocess((arg) => Number(arg), z.number().min(1).default(1))
      .optional(),
  }),
});

const getTeamValidationSchema = z.object({
  params: z.object({
    teamId: z.string().min(1, 'Team ID is required'),
  }),
});

const updateTeamValidationSchema = z.object({
  body: z.object({
    name: z.string().optional(),
    description: z.string().optional(),
  }),
});

const deleteTeamValidationSchema = z.object({
  params: z.object({
    teamId: z.string().min(1, 'Team ID is required'),
  }),
});

const addUsersToTeamValidationSchema = z.object({
  params: z.object({
    teamId: z.string().min(1, 'Team ID is required'),
  }),
  body: z.object({
    userIds: z.array(z.string()).min(1, 'User IDs are required'),
  }),
});


const removeUsersFromTeamValidationSchema = z.object({
  params: z.object({
    teamId: z.string().min(1, 'Team ID is required'),
  }),
  body: z.object({
    userIds: z.array(z.string()).min(1, 'User IDs are required'),
  }).optional(),
});


const updateTeamUsersPermissionsValidationSchema = z.object({
  params: z.object({
    teamId: z.string().min(1, 'Team ID is required'),
  }),
  body: z.object({
    userIds: z.array(z.string()).min(1, 'User IDs are required'),
    role: z.string().optional(),
  }),
});

const getTeamUsersValidationSchema = z.object({
  params: z.object({
    teamId: z.string().min(1, 'Team ID is required'),
  }),
});

export function createTeamsRouter(container: Container) {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');

  router.post(
    '/',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(createTeamValidationSchema),
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
    '/',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(listTeamsValidationSchema),
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const teamsController =
          container.get<TeamsController>('TeamsController');
        await teamsController.listTeams(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/:teamId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(getTeamValidationSchema),
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
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateTeamValidationSchema),
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
    metricsMiddleware(container),
    ValidationMiddleware.validate(deleteTeamValidationSchema),
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
    metricsMiddleware(container),
    ValidationMiddleware.validate(getTeamUsersValidationSchema),
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const teamsController = container.get<TeamsController>('TeamsController');
        await teamsController.getTeamUsers(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.post(
    '/:teamId/users',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(addUsersToTeamValidationSchema),
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const teamsController = container.get<TeamsController>('TeamsController');
        await teamsController.addUsersToTeam(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.delete(
    '/:teamId/users',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(removeUsersFromTeamValidationSchema),
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const teamsController = container.get<TeamsController>('TeamsController');
        await teamsController.removeUserFromTeam(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.put(
    '/:teamId/users/permissions',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateTeamUsersPermissionsValidationSchema),
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const teamsController = container.get<TeamsController>('TeamsController');
        await teamsController.updateTeamUsersPermissions(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/user/teams',
    authMiddleware.authenticate,
    metricsMiddleware(container),
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
