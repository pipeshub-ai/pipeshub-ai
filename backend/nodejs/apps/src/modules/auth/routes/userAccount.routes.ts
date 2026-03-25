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
  'cf-turnstile-response': z.string().optional(), // Add Turnstile token for forgot password
});

const otpGenerationValidationSchema = z.object({
  body: otpGenerationBody,
  query: z.object({}),
  params: z.object({}),
  headers: z.object({}),
});

const initAuthBody = z.object({
  email: z
    .string()
    // .min(1, 'Email is required')
    .max(254, 'Email address is too long') // RFC 5321 limit
    .email('Invalid email format').optional(),
});

const initAuthValidationSchema = z.object({
  body: initAuthBody,
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
    ValidationMiddleware.validate(initAuthValidationSchema),
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
  const authenticateBody = z.object({
    method: z.string().min(1, 'Authentication method is required'),
    credentials: z.union([
      z.string().min(1, 'Credentials cannot be empty'), // For Google OAuth, credentials can be a string (ID token)
      z.object({
        password: z.string().optional(),
        otp: z.string().optional(),
        token: z.string().optional(),
        code: z.string().optional(),
        accessToken: z.string().optional(),
        idToken: z.string().optional(),
      }).passthrough(), // Allow additional fields for OAuth providers
    ]),
    email: z
      .string()
      .max(254, 'Email address is too long') // RFC 5321 limit
      .email('Invalid email format')
      .optional(),
    'cf-turnstile-response': z.string().optional(), // Add Turnstile token
  }).strict();

  const authenticateValidationSchema = z.object({
    body: authenticateBody,
    query: z.object({}),
    params: z.object({}),
    headers: z.object({}),
  });

  router.post(
    '/authenticate',
    authSessionMiddleware,
    ValidationMiddleware.validate(authenticateValidationSchema),
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

  const resetPasswordValidationSchema = z.object({
    body: z.object({
      currentPassword: z.string(),
      newPassword: z.string(),
      'cf-turnstile-response': z.string().optional(),
    }),
  });

  router.post(
    '/password/reset',
    userValidator,
    ValidationMiddleware.validate(resetPasswordValidationSchema),
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
