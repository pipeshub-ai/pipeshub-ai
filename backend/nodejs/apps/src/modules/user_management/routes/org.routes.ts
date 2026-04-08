import { Router, Request, Response, NextFunction } from 'express';
import { Container } from 'inversify';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';

import { userAdminCheck } from '../middlewares/userAdminCheck';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { OrgController } from '../controller/org.controller';
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';
import { attachContainerMiddleware } from '../../auth/middlewares/attachContainer.middleware';
import { FileProcessorFactory } from '../../../libs/middlewares/file_processor/fp.factory';
import { FileProcessingType } from '../../../libs/middlewares/file_processor/fp.constant';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import {
  OrgCreationValidationSchema,
  OnboardingStatusUpdateValidationSchema,
  OrgUpdateValidationSchema,
  OrgHealthResponseSchema,
  OrgLogoPutValidationSchema,
  OrgLogoReadDeleteValidationSchema,
} from '../schemas/org.schemas';
import { sendValidatedJson } from '../../../utils/response-validator';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';

export function createOrgRouter(container: Container) {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');

  router.use(attachContainerMiddleware(container));

  router.get(
    '/exists',
    async (_req: Request, res: Response, next: NextFunction) => {
      try {
        const orgController = container.get<OrgController>('OrgController');
        await orgController.checkOrgExistence(res);
      } catch (error) {
        next(error);
      }
    },
  );

  router.post(
    '/',
    ValidationMiddleware.validate(OrgCreationValidationSchema),
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const orgController = container.get<OrgController>('OrgController');
        await orgController.createOrg(req, res);
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.ORG_READ),
    metricsMiddleware(container),
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const orgController = container.get<OrgController>('OrgController');
        await orgController.getOrganizationById(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.put(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.ORG_WRITE),
    metricsMiddleware(container),
    userAdminCheck,
    ValidationMiddleware.validate(OrgUpdateValidationSchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const orgController = container.get<OrgController>('OrgController');
        await orgController.updateOrganizationDetails(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.delete(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.ORG_ADMIN),
    metricsMiddleware(container),
    userAdminCheck,
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const orgController = container.get<OrgController>('OrgController');
        await orgController.deleteOrganization(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.put(
    '/logo',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.ORG_WRITE),
    ...FileProcessorFactory.createBufferUploadProcessor({
      fieldName: 'file',
      allowedMimeTypes: [
        'image/png',
        'image/jpeg',
        'image/jpg',
        'image/webp',
        'image/gif',
        'image/svg+xml',
      ],
      maxFilesAllowed: 1,
      isMultipleFilesAllowed: false,
      processingType: FileProcessingType.BUFFER,
      maxFileSize: 1024 * 1024,
      strictFileUpload: true,
    }).getMiddleware,
    metricsMiddleware(container),
    userAdminCheck,
    ValidationMiddleware.validate(OrgLogoPutValidationSchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const orgController = container.get<OrgController>('OrgController');
        await orgController.updateOrgLogo(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );
  router.delete(
    '/logo',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.ORG_WRITE),
    metricsMiddleware(container),
    userAdminCheck,
    ValidationMiddleware.validate(OrgLogoReadDeleteValidationSchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const orgController = container.get<OrgController>('OrgController');
        await orgController.removeOrgLogo(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );
  router.get(
    '/logo',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.ORG_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(OrgLogoReadDeleteValidationSchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const orgController = container.get<OrgController>('OrgController');
        await orgController.getOrgLogo(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/onboarding-status',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.ORG_READ),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const orgController = container.get<OrgController>('OrgController');
        await orgController.getOnboardingStatus(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.put(
    '/onboarding-status',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.ORG_WRITE),
    userAdminCheck,
    ValidationMiddleware.validate(OnboardingStatusUpdateValidationSchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const orgController = container.get<OrgController>('OrgController');
        await orgController.updateOnboardingStatus(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  // Health check endpoint
  router.get('/health', (_req: Request, res: Response) => {
    sendValidatedJson(
      res,
      OrgHealthResponseSchema,
      {
        status: 'healthy',
        timestamp: new Date().toISOString(),
      },
      HTTP_STATUS.OK,
    );
  });

  return router;
}
