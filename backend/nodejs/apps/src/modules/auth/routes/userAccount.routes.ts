import { Router, Request, Response, NextFunction } from 'express';
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
import {
  createAuthIpRateLimiter,
  createAuthRateLimiter,
  createInitAuthRateLimiter,
} from '../../../libs/middlewares/rate-limit.middleware';
import { Logger } from '../../../libs/services/logger.service';
import { AppConfig, loadAppConfig } from '../../tokens_manager/config/config';
import {
  AUTH_APP_CONFIG_RELOADER,
  AuthAppConfigReloader,
} from '../container/authService.container';
import { emailCheck } from '../../../libs/utils/zod-formats';
import { AuthMethodType } from '../schema/orgAuthConfiguration.schema';

const otpGenerationBody = z.object({
  email: z.string().check(emailCheck('Invalid email')),
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
    .check(emailCheck('Invalid email format'))
    .optional(),
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
  const logger = container.get<Logger>('Logger');
  const { maxAuthRequestsPerMinute } = container.get<AppConfig>('AppConfig');
  // Shared by all credential/OTP/email routes below: one bucket per account
  // (or signed-in user), plus a looser per-IP backstop on anonymous routes.
  const authRateLimiter = createAuthRateLimiter(
    logger,
    maxAuthRequestsPerMinute,
  );
  const authIpRateLimiter = createAuthIpRateLimiter(
    logger,
    maxAuthRequestsPerMinute,
  );
  // A provider sign-in (Google, Microsoft, OAuth) carries a token the
  // provider verifies, so there is nothing to guess, and it usually names no
  // email: the account limiter would put everyone on one IP in one bucket.
  // Those count against the per-IP backstop only; every other method,
  // including an unknown one, is counted per account.
  const providerVerifiedMethods = new Set<string>([
    AuthMethodType.GOOGLE,
    AuthMethodType.MICROSOFT,
    AuthMethodType.AZURE_AD,
    AuthMethodType.OAUTH,
    AuthMethodType.GITHUB,
  ]);
  const authenticateRateLimiter = (
    req: Request,
    res: Response,
    next: NextFunction,
  ): void => {
    const method: unknown = (req.body as { method?: unknown } | undefined)
      ?.method;
    if (typeof method === 'string' && providerVerifiedMethods.has(method)) {
      next();
      return;
    }
    void authRateLimiter(req, res, next);
  };

  router.post(
    '/initAuth',
    createInitAuthRateLimiter(logger, maxAuthRequestsPerMinute),
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
      }).loose(), // Allow additional fields for OAuth providers
    ]),
    email: z
      .string()
      .max(254, 'Email address is too long') // RFC 5321 limit
      .check(emailCheck('Invalid email format'))
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
    authIpRateLimiter,
    authSessionMiddleware,
    authenticateRateLimiter,
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
    authIpRateLimiter,
    authRateLimiter,
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
      currentPassword: z.string().optional(),
      newPassword: z.string(),
      'cf-turnstile-response': z.string().optional(),
    }),
  });

  router.get(
    '/password/status',
    userValidator,
    async (req: AuthSessionRequest, res: Response, next: NextFunction) => {
      try {
        const userAccountController = container.get<UserAccountController>(
          'UserAccountController',
        );
        await userAccountController.getPasswordStatus(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.post(
    '/password/setup/email',
    userValidator,
    authRateLimiter,
    ValidationMiddleware.validate(z.object({ body: z.object({}).optional() })),
    async (req: AuthSessionRequest, res: Response, next: NextFunction) => {
      try {
        const userAccountController = container.get<UserAccountController>(
          'UserAccountController',
        );
        await userAccountController.sendPasswordSetupEmail(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.post(
    '/password/reset',
    userValidator,
    authRateLimiter,
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
    authIpRateLimiter,
    authRateLimiter,
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

  router.put(
    '/validateEmailChange',
    authMiddleware.scopedTokenValidator(TokenScopes.VALIDATE_EMAIL),
    async (req: AuthSessionRequest, res: Response, next: NextFunction) => {
      try {
        const userAccountController = container.get<UserAccountController>(
          'UserAccountController',
        );
        await userAccountController.validateEmailChange(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  // Called by the configuration manager after settings such as the frontend URL change.
  router.post(
    '/updateAppConfig',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    async (
      _req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        container.get<AuthAppConfigReloader>(AUTH_APP_CONFIG_RELOADER)(
          await loadAppConfig(),
        );
        res
          .status(200)
          .json({ message: 'Auth configuration updated successfully' });
      } catch (error) {
        next(error);
      }
    },
  );
  return router;
}
