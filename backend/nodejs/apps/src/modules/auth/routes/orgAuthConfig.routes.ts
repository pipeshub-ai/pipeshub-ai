import { Router, Response, NextFunction } from 'express';
import { Container } from 'inversify';
import {
  adminValidator,
  userValidator,
} from '../middlewares/userAuthentication.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { AuthSessionRequest } from '../middlewares/types';
import { attachContainerMiddleware } from '../middlewares/attachContainer.middleware';
import { UserAccountController } from '../controller/userAccount.controller';
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';
import {
  GetAuthMethodsValidationSchema,
  SetUpAuthConfigValidationSchema,
  UpdateAuthMethodValidationSchema,
} from '../validation/orgAuth-validation';

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
    ValidationMiddleware.validate(GetAuthMethodsValidationSchema),
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
    ValidationMiddleware.validate(SetUpAuthConfigValidationSchema),
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
    ValidationMiddleware.validate(UpdateAuthMethodValidationSchema),
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
