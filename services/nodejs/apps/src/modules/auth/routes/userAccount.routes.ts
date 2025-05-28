import { Router, Response, NextFunction } from 'express';
import { z } from 'zod';
import { Container } from 'inversify';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';

import {
  authSessionMiddleware,
  userValidator,
} from '../middlewares/userAuthentication.middleware';
import { attachContainerMiddleware } from '../middlewares/attachContainer.middleware';
import { AuthSessionRequest } from '../middlewares/types';
import { UserAccountController } from '../controller/userAccount.controller';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { AuthenticatedServiceRequest } from '../../../libs/middlewares/types';

const otpGenerationBody = z.object({
  email: z.string().email('Invalid email'),
});

const otpGenerationValidationSchema = z.object({
  body: otpGenerationBody,
  query: z.object({}),
  params: z.object({}),
  headers: z.object({}),
});

export function createUserAccountRouter(container: Container) {
  const router = Router();

  router.use(attachContainerMiddleware(container));
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');

  router.post(
    '/initAuth',
    async (req: AuthSessionRequest, res: Response, next: NextFunction) => {
      try {
        const userAccountController = container.get<UserAccountController>(
          'UserAccountController',
        );
        await userAccountController.initAuth(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );
  router.post(
    '/authenticate',
    authSessionMiddleware,
    async (req: AuthSessionRequest, res: Response, next: NextFunction) => {
      try {
        const userAccountController = container.get<UserAccountController>(
          'UserAccountController',
        );
        await userAccountController.authenticate(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.post(
    '/login/otp/generate',
    ValidationMiddleware.validate(otpGenerationValidationSchema),
    async (req: AuthSessionRequest, res: Response, next: NextFunction) => {
      try {
        const userAccountController = container.get<UserAccountController>(
          'UserAccountController',
        );
        await userAccountController.getLoginOtp(req, res);
      } catch (error) {
        next(error);
      }
    },
  );

  router.post(
    '/password/reset',
    userValidator,
    async (req: AuthSessionRequest, res: Response, next: NextFunction) => {
      try {
        const userAccountController = container.get<UserAccountController>(
          'UserAccountController',
        );
        await userAccountController.resetPassword(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.post(
    '/refresh/token',
    authMiddleware.scopedTokenValidator(TokenScopes.TOKEN_REFRESH),
    async (req: AuthSessionRequest, res: Response, next: NextFunction) => {
      try {
        const userAccountController = container.get<UserAccountController>(
          'UserAccountController',
        );
        await userAccountController.getAccessTokenFromRefreshToken(
          req,
          res,
          next,
        );
      } catch (error) {
        next(error);
      }
    },
  );

  router.post(
    '/logout/manual',
    userValidator,
    async (req: AuthSessionRequest, res: Response, next: NextFunction) => {
      try {
        const userAccountController = container.get<UserAccountController>(
          'UserAccountController',
        );
        await userAccountController.logoutSession(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );
  router.post(
    '/password/reset/token',
    authMiddleware.scopedTokenValidator(TokenScopes.PASSWORD_RESET),
    async (
      req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userAccountController = container.get<UserAccountController>(
          'UserAccountController',
        );
        await userAccountController.resetPasswordViaEmailLink(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.post(
    '/password/forgot',
    async (req: AuthSessionRequest, res: Response, next: NextFunction) => {
      try {
        const userAccountController = container.get<UserAccountController>(
          'UserAccountController',
        );
        await userAccountController.forgotPasswordEmail(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  //sending mail for setting password for the first time
  router.get(
    '/internal/password/check',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    async (
      req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userAccountController = container.get<UserAccountController>(
          'UserAccountController',
        );
        await userAccountController.hasPasswordMethod(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.post(
    '/oauth/exchange',
    async (req: AuthSessionRequest, res: Response, next: NextFunction) => {
      try {
        const userAccountController = container.get<UserAccountController>(
          'UserAccountController',
        );
        await userAccountController.exchangeOAuthToken(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  // router.post('/setup', resetViaLinkValidator, userAccountSetup);
  return router;
}
