import { Router, Response, NextFunction } from 'express';
import { Container } from 'inversify';
import {
  adminValidator,
  userValidator,
} from '../middlewares/userAuthentication.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { z } from 'zod';
import { AuthMethodType } from '../schema/orgAuthConfiguration.schema';
import { AuthSessionRequest } from '../middlewares/types';
import { attachContainerMiddleware } from '../middlewares/attachContainer.middleware';
import { UserAccountController } from '../controller/userAccount.controller';
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';
const authMethodSchema = z.object({
  type: z.nativeEnum(AuthMethodType),
});
const authStepSchema = z.object({
  order: z.number(),
  allowedMethods: z
    .array(authMethodSchema)
    .nonempty('At least one method is required')
    .superRefine((methods, ctx) => {
      const methodSet = new Set();
      for (const method of methods) {
        if (methodSet.has(method.type)) {
          ctx.addIssue({
            code: 'custom',
            message: `Duplicate authentication method "${method.type}" in the same step`,
          });
        }
        methodSet.add(method.type);
      }
    }),
});

// Custom validation for authSteps
const authStepsSchema = z
  .array(authStepSchema)
  .min(1, 'At least one authentication step is required')
  .max(3, 'A maximum of 3 authentication steps is allowed')
  .superRefine((steps, ctx) => {
    const orderSet = new Set();
    const globalMethodSet = new Set();

    for (const step of steps) {
      // Ensure unique step order
      if (orderSet.has(step.order)) {
        ctx.addIssue({
          code: 'custom',
          message: `Duplicate order found: ${step.order}`,
        });
      }
      orderSet.add(step.order);

      // Ensure unique authentication methods across all steps
      for (const method of step.allowedMethods) {
        if (globalMethodSet.has(method.type)) {
          ctx.addIssue({
            code: 'custom',
            message: `Authentication method "${method.type}" is repeated across multiple steps`,
          });
        }
        globalMethodSet.add(method.type);
      }
    }
  });
const authMethodValidationBody = z.object({
  authMethod: authStepsSchema,
});
const authMethodValidationSchema = z.object({
  body: authMethodValidationBody,
  query: z.object({}),
  params: z.object({}),
  headers: z.object({}),
});

export function createOrgAuthConfigRouter(container: Container) {
  const router = Router();
  router.use(attachContainerMiddleware(container));
  const userAccountController = container.get<UserAccountController>(
    'UserAccountController',
  );
  router.get(
    '/authMethods',
    userValidator,
    adminValidator,
    metricsMiddleware(container),
    async (req: AuthSessionRequest, res: Response, next: NextFunction) => {
      try {
        await userAccountController.getAuthMethod(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );
  router.post(
    '/',
    userValidator,
    adminValidator,
    async (req: AuthSessionRequest, res: Response, next: NextFunction) => {
      try {
        await userAccountController.setUpAuthConfig(req, res);
      } catch (error) {
        next(error);
      }
    },
  );
  router.post(
    '/updateAuthMethod',
    userValidator,
    adminValidator,
    ValidationMiddleware.validate(authMethodValidationSchema),
    metricsMiddleware(container),
    async (req: AuthSessionRequest, res: Response, next: NextFunction) => {
      try {
        await userAccountController.updateAuthMethod(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  return router;
}
