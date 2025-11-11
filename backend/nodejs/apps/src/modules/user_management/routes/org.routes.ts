import { Router, Request, Response, NextFunction } from 'express';
import { z } from 'zod';
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

const OrgCreationBody = z
  .object({
    accountType: z.enum(['individual', 'business']),
    shortName: z.string().optional(),
    contactEmail: z.string().email('Invalid email format'),
    registeredName: z.string().optional(), // Will be enforced conditionally
    adminFullName: z.string().min(1, 'Admin full name required'),
    password: z.string().min(8, 'Minimum 8 characters password required'),
    sendEmail: z.boolean().optional(),
    permanentAddress: z
      .object({
        addressLine1: z.string().optional(),
        city: z.string().optional(),
        state: z.string().optional(),
        country: z.string().optional(),
        postCode: z.string().optional(),
      })
      .optional(),
  })
  .refine(
    (data) => {
      // If accountType is 'business', registeredName must be present
      return data.accountType === 'business' ? !!data.registeredName : true;
    },
    {
      message: 'Registered Name is required for business accounts',
      path: ['registeredName'], // This ensures the error is associated with registeredName
    },
  );

const OnboardingStatusUpdateBody = z.object({
  status: z.enum(['configured', 'notConfigured', 'skipped']),
});

const OnboardingStatusUpdateValidationSchema = z.object({
  body: OnboardingStatusUpdateBody,
  query: z.object({}),
  params: z.object({}),
  headers: z.object({}),
});

const OrgCreationValidationSchema = z.object({
  body: OrgCreationBody,
  query: z.object({}),
  params: z.object({}),
  headers: z.object({}),
});

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
    metricsMiddleware(container),
    userAdminCheck,
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
    ...FileProcessorFactory.createBufferUploadProcessor({
      fieldName: 'file',
      allowedMimeTypes: [
        'image/png',
        'image/jpeg',
        'image/jpg',
        'image/webp',
        'image/gif',
      ],
      maxFilesAllowed: 1,
      isMultipleFilesAllowed: false,
      processingType: FileProcessingType.BUFFER,
      maxFileSize: 1024 * 1024,
      strictFileUpload: true,
    }).getMiddleware,
    metricsMiddleware(container),
    userAdminCheck,
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
    metricsMiddleware(container),
    userAdminCheck,
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
    metricsMiddleware(container),
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
    res.json({
      status: 'healthy',
      timestamp: new Date().toISOString(),
    });
  });

  return router;
}
