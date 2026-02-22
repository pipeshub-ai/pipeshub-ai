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
import { JitProvisioningService } from '../services/jit-provisioning.service';
import { UserActivities } from '../schema/userActivities.schema';
import { userActivitiesType } from '../../../libs/utils/userActivities.utils';

const isValidEmail = (email: string) => {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email); // Basic email regex
};
const {
  LOGIN
} = userActivitiesType;

export function createSamlRouter(container: Container) {
  const router = Router();

  let config = container.get<AppConfig>('AppConfig');
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const sessionService = container.get<SessionService>('SessionService');
  const iamService = container.get<IamService>('IamService');
  const samlController = container.get<SamlController>('SamlController');
  const jitProvisioningService = container.get<JitProvisioningService>('JitProvisioningService');
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
    "/signIn/callback",
    passport.authenticate("saml", { failureRedirect: "/" }),
    async (
      req: AuthSessionRequest,
      res: Response,
      next: NextFunction
    ): Promise<void> => {
      let session: any;
      let samlUser: Record<string, any>;
      let user: any = null;
      let userDetails: any;

      try {
        /* ---------------- RelayState ---------------- */
        if (!req.user) {
          throw new NotFoundError("User not available in SAML response");
        }
        const relayStateBase64 =
          (req.body?.RelayState as string) ||
          (req.query?.RelayState as string);

        const relayState: {
          orgId?: string;
          sessionToken?: string;
        } = relayStateBase64
            ? JSON.parse(
              Buffer.from(relayStateBase64, "base64").toString("utf8")
            )
            : {};

        const { orgId, sessionToken } = relayState;

        if (!sessionToken) {
          throw new UnauthorizedError("Invalid session token");
        }

        /* ---------------- Session ---------------- */

        session = await sessionService.getSession(sessionToken);




        if (!session) {
          throw new UnauthorizedError("Invalid session");
        }

        req.sessionInfo = session;

        const method = "samlSso";

        const currentStepConfig =
          session.authConfig[session.currentStep];

        if (
          !currentStepConfig.allowedMethods.find(
            (m: { type: string }) => m.type === method
          )
        ) {
          throw new BadRequestError(
            "Invalid authentication method for this step"
          );
        }

        /* ---------------- SAML Email ---------------- */

        samlUser = req.user as Record<string, any>;

        if (!orgId) {
          throw new BadRequestError(
            "Organization ID not found in session"
          );
        }

        const emailKey =
          samlController.getSamlEmailKeyByOrgId(orgId);


        samlUser.email = samlUser[emailKey];


        const fallbackKeys: string[] = [
          "email",
          "mail",
          "userPrincipalName",
          "nameID",
          "primaryEmail",
          "contactEmail",
          "preferred_username",
          "mailPrimaryAddress",
        ];

        if (!isValidEmail(samlUser.email)) {
          for (const k of fallbackKeys) {
            if (samlUser[k] && isValidEmail(samlUser[k])) {
              samlUser.email = samlUser[k];
              break;
            }
          }
        }

        if (session?.email !== samlUser?.email) {
          res.redirect(
            `${config.frontendUrl}/auth/sign-in?error=incorrect_email`
          );

        }

        if (!samlUser.email) {
          throw new InternalServerError(
            "Valid email not found in SAML response"
          );
        }

        /* ================= JIT ================= */

        if (session.userId === "NOT_FOUND") {
          const jitConfig = session.jitConfig as
            | Record<string, boolean>
            | undefined;

          const methodKey = method;

          if (!jitConfig || !jitConfig[methodKey]) {
            if (!session.orgId) {
              throw new BadRequestError(
                "Organization not found for JIT provisioning"
              );
            }
          }

          userDetails =
            jitProvisioningService.extractSamlUserDetails(
              samlUser,
              samlUser.email
            );

          user = await jitProvisioningService.provisionUser(
            samlUser.email,
            userDetails,
            session.orgId,
            "saml"
          );

          await UserActivities.create({
            email: samlUser.email,
            activityType: LOGIN,
            ipAddress: req.ip,
            loginMode: "SSO",
          });
        }

        /* ============ Existing User ============ */

        if (!user) {
          const authToken = iamJwtGenerator(
            samlUser.email,
            config.scopedJwtSecret
          );

          const result =
            await iamService.getUserByEmail(
              samlUser.email,
              authToken
            );

          if (!result) {
            throw new NotFoundError("User not found");
          }

          user = result.data;
        }

        /* -------------- Finalize -------------- */

        if (!session) {
          throw new UnauthorizedError("Session not found");
        }

        await sessionService.completeAuthentication(
          session
        );

        const accessToken = await generateAuthToken(
          user,
          config.jwtSecret
        );

        const refreshToken = refreshTokenJwtGenerator(
          user._id,
          session.orgId,
          config.scopedJwtSecret
        );

        if (!user.hasLoggedIn && user._id) {
          await iamService.updateUser(
            user._id,
            { hasLoggedIn: true },
            accessToken
          );
        }

        res.cookie("accessToken", accessToken, {
          secure: true,
          sameSite: "none",
          maxAge: 60 * 60 * 1000,
        });

        res.cookie("refreshToken", refreshToken, {
          secure: true,
          sameSite: "none",
          maxAge: 7 * 24 * 60 * 60 * 1000,
        });

        res.redirect(
          `${config.frontendUrl}/auth/sign-in/samlSso/success`
        );
      } catch (error) {
        next(error);
      }
    }
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
              container.get<JitProvisioningService>('JitProvisioningService'),
            );
          });
        container
          .rebind<SamlController>('SamlController')
          .toDynamicValue(() => {
            return new SamlController(
              container.get<IamService>('IamService'),
              config,
              logger,
              container.get<ConfigurationManagerService>(
                'ConfigurationManagerService'),
              container.get<SessionService>('SessionService'),


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
