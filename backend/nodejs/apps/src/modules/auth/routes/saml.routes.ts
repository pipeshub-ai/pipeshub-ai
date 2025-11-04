import { Router, Response, NextFunction } from 'express';
import { Container } from 'inversify';

import passport from 'passport';
import session from 'express-session';
import { attachContainerMiddleware } from '../middlewares/attachContainer.middleware';
import { AuthSessionRequest } from '../middlewares/types';
import {
  iamJwtGenerator,
  refreshTokenJwtGenerator,
} from '../../../libs/utils/createJwt';
import { IamService } from '../services/iam.service';
import {
  BadRequestError,
  InternalServerError,
  NotFoundError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { SessionService } from '../services/session.service';
import { SamlController } from '../controller/saml.controller';
import { Logger } from '../../../libs/services/logger.service';
import { generateAuthToken } from '../utils/generateAuthToken';
import { AppConfig, loadAppConfig } from '../../tokens_manager/config/config';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AuthenticatedServiceRequest } from '../../../libs/middlewares/types';
import { UserAccountController } from '../controller/userAccount.controller';
import { MailService } from '../services/mail.service';
import { ConfigurationManagerService } from '../services/cm.service';
import { UserController } from '../../user_management/controller/users.controller';

const isValidEmail = (email: string) => {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email); // Basic email regex
};

export function createSamlRouter(container: Container, userManagerContainer: Container) {
  const router = Router();

  let config = container.get<AppConfig>('AppConfig');
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const sessionService = container.get<SessionService>('SessionService');
  const iamService = container.get<IamService>('IamService');
  const samlController = container.get<SamlController>('SamlController');
  const userController = userManagerContainer.get<UserController>('UserController');
  const logger = container.get<Logger>('Logger');
  router.use(attachContainerMiddleware(container));
  router.use(
    session({
      secret: config.cookieSecret,
      resave: true,
      saveUninitialized: true,
      cookie: {
        maxAge: 60 * 60 * 1000, // 1 hour
        domain: 'localhost',
        secure: false, // Set to `true` if using HTTPS
        sameSite: 'lax',
      },
    }),
  );
  router.use(passport.initialize());
  router.use(passport.session());

  router.get(
    '/signIn',
    async (req: AuthSessionRequest, res: Response, next: NextFunction) => {
      try {
        await samlController.signInViaSAML(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.post(
    '/signIn/callback',
    passport.authenticate('saml', { failureRedirect: '/' }),
    async (req: AuthSessionRequest, res: Response, next: NextFunction) => {
      try {
        const relayStateBase64 = req.body.RelayState || req.query.RelayState;
        const relayStateDecoded = relayStateBase64
          ? JSON.parse(Buffer.from(relayStateBase64, 'base64').toString('utf8'))
          : {};

        const orgId = relayStateDecoded.orgId;
        const sessionToken = relayStateDecoded.sessionToken;

        if (!sessionToken) {
          throw new UnauthorizedError('Invalid Session token');
        }
        const method = 'samlSso';
        const session = await sessionService.getSession(sessionToken);
        if (!session) {
          throw new UnauthorizedError('Invalid Session');
        }
        req.sessionInfo = session;

        const currentStepConfig =
          req.sessionInfo.authConfig[req.sessionInfo.currentStep];
        logger.info(currentStepConfig, 'currentStepConfig');

        if (
          !currentStepConfig.allowedMethods.find((m: any) => m.type === method)
        ) {
          throw new BadRequestError(
            'Invalid authentication method for this step',
          );
        }

        // Todo: check if User Account exists and validate if user is not blocked
        if (!req.user) {
          throw new NotFoundError('User not found');
        }
        if (req.user) {
          const key = samlController.getSamlEmailKeyByOrgId(orgId);
          req.user.email = req.user[key];
          if (!isValidEmail(req.user.email)) {
            const possibleEmailKeys = [
              'email',
              'mail',
              'userPrincipalName',
              'nameID',
              'EmailAddress',
              'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress',
              'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn',
              'urn:oid:0.9.2342.19200300.100.1.3',
              'primaryEmail',
              'contactEmail',
              'preferred_username',
              'mailPrimaryAddress',
            ];

            // Check other keys if the email is missing or invalid
            for (const k of possibleEmailKeys) {
              if (!req.user.email || !isValidEmail(req.user.email)) {
                if (req.user[k] && isValidEmail(req.user[k])) {
                  req.user.email = req.user[k];
                  break; // Stop once a valid email is found
                }
              }
            }
          }

          if (!req.user.email) {
            throw new InternalServerError(
              'Valid Email ID not found in SAML response',
            );
          }
          logger.info(req.user.email);

          logger.info('SAML callback email', req.user.email);
        }
        // Validate orgId is available
        if (!orgId) {
          throw new BadRequestError('Organization ID not found in session');
        }

        const authToken = iamJwtGenerator(
          req.user.email,
          config.scopedJwtSecret,
        );

        // Try to find existing user, if not found, auto-provision via JIT
        let user;
        try {
          const userFindResult = await iamService.getUserByEmail(
            req.user.email,
            authToken,
          );
          if (!userFindResult) {
            throw new NotFoundError('User not found');
          }
          user = userFindResult.data;
        } catch (error: any) {
          if (error instanceof NotFoundError) {
            // JIT Provisioning: Auto-create user from SAML assertion
            user = await userController.provisionSamlUser(
              req.user.email,
              req.user,
              orgId,
              logger,
            );
          } else {
            // For errors other than NotFound, re-throw to be handled by the generic error handler
            throw error;
          }
        }
        const userId = user._id;

        await sessionService.completeAuthentication(req.sessionInfo);
        const accessToken = await generateAuthToken(user, config.jwtSecret);
        const refreshToken = refreshTokenJwtGenerator(
          userId,
          orgId,
          config.scopedJwtSecret,
        );
        if (!user.hasLoggedIn) {
          const userInfo = {
            ...user,
            hasLoggedIn: true,
          };
          iamService.updateUser(user._id, userInfo, accessToken);
          logger.info('user updated');
        }
        res.cookie('accessToken', accessToken, {
          // httpOnly: true,
          secure: true, // use true in production with HTTPS
          sameSite: 'none', // adjust as needed
          maxAge: 3600000, // 1 hour in milliseconds
        });
        res.cookie('refreshToken', refreshToken, {
          // httpOnly: true,
          secure: true, // Set to true in production (HTTPS)
          sameSite: 'none', // Adjust as needed
          maxAge: 7 * 24 * 60 * 60 * 1000, // 7 days in milliseconds
        });
        logger.info('completely authenticated');
        res
          .status(200)
          .redirect(`${config.frontendUrl}/auth/sign-in/samlSso/success`);
      } catch (error) {
        next(error);
      }

      // Todo: check if User Account exists and validate if user is not blocked
    },
  );

  router.post(
    '/updateAppConfig',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    async (
      _req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        config = await loadAppConfig();

        container.rebind<AppConfig>('AppConfig').toDynamicValue(() => config);

        container
          .rebind<UserAccountController>('UserAccountController')
          .toDynamicValue(() => {
            return new UserAccountController(
              config,
              container.get<IamService>('IamService'),
              container.get<MailService>('MailService'),
              container.get<SessionService>('SessionService'),
              container.get<ConfigurationManagerService>(
                'ConfigurationManagerService',
              ),
              logger,
            );
          });
        container
          .rebind<SamlController>('SamlController')
          .toDynamicValue(() => {
            return new SamlController(
              container.get<IamService>('IamService'),
              config,
              logger,
            );
          });
        res.status(200).json({
          message: 'Auth configuration updated successfully',
          config,
        });
        return;
      } catch (error) {
        next(error);
      }
    },
  );
  return router;
}
