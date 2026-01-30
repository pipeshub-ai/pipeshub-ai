import { Router, Request, Response, NextFunction } from 'express';
import { z } from 'zod';
import { Container } from 'inversify';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';

import { TeamsController } from '../controller/teams.controller';

const createTeamValidationSchema = z.object({
  body: z.preprocess(
    (data: any) => {
      // Support both new format (userRoles) and legacy format (userIds + role)
      if (data?.userRoles && Array.isArray(data.userRoles)) {
        // Clean up userRoles array
        data.userRoles = data.userRoles.filter(
          (ur: any) => ur && ur.userId && typeof ur.userId === 'string' && ur.userId.trim() !== '' && ur.role
        );
        if (data.userRoles.length === 0) {
          delete data.userRoles;
        }
      } else if (data?.userIds && Array.isArray(data.userIds)) {
        // Legacy format: clean up userIds array
        data.userIds = data.userIds.filter(
          (id: any) => id !== null && id !== undefined && id !== '' && typeof id === 'string' && id.trim() !== ''
        );
        if (data.userIds.length === 0) {
          delete data.userIds;
        }
      }
      return data;
    },
    z.object({
      name: z.string().min(1, 'Name is required'),
      description: z.string().optional(),
      userIds: z.array(z.string().min(1)).optional(), // Legacy format
      role: z.string().optional(), // Legacy format
      userRoles: z.array(z.object({
        userId: z.string().min(1),
        role: z.string().min(1),
      })).optional(), // New format
    })
  ),
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
    addUserIds: z.array(z.string()).optional(), // Legacy format
    addUserRoles: z.array(z.object({
      userId: z.string().min(1),
      role: z.string().min(1),
    })).optional(), // New format
    removeUserIds: z.array(z.string()).optional(),
    role: z.string().optional(), // Legacy format
    updateUserRoles: z.array(z.object({
      userId: z.string().min(1),
      role: z.string().min(1),
    })).optional(), // New format for updating existing user roles
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
    userIds: z.array(z.string()).min(1, 'User IDs are required').optional(), // Legacy format
    role: z.string().optional(), // Legacy format
    userRoles: z.array(z.object({
      userId: z.string().min(1),
      role: z.string().min(1),
    })).min(1, 'User roles are required').optional(), // New format
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
    userIds: z.array(z.string()).min(1, 'User IDs are required').optional(), // Legacy format
    role: z.string().optional(), // Legacy format
    userRoles: z.array(z.object({
      userId: z.string().min(1),
      role: z.string().min(1),
    })).min(1, 'User roles are required').optional(), // New format
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

  router.get(
    '/user/teams/created',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(listTeamsValidationSchema),
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const teamsController = container.get<TeamsController>('TeamsController');
        await teamsController.getUserCreatedTeams(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  return router;
}
