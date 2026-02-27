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
  NotFoundError,
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
import { ConfigurationManagerService, SSO_AUTH_CONFIG_PATH } from '../services/cm.service';
import { JitProvisioningService } from '../services/jit-provisioning.service';
import { OrgAuthConfig } from '../schema/orgAuthConfiguration.schema';
import { Org } from '../../user_management/schema/org.schema';

export const isValidEmail = (email: string) => {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email); // Basic email regex
};


export function createSamlRouter(container: Container) {
  const router = Router();

  let config = container.get<AppConfig>('AppConfig');
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const sessionService = container.get<SessionService>('SessionService');
  const iamService = container.get<IamService>('IamService');
  const samlController = container.get<SamlController>('SamlController');
  const jitProvisioningService = container.get<JitProvisioningService>('JitProvisioningService');
  const configurationManagerService = container.get<ConfigurationManagerService>('ConfigurationManagerService');

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




  // Helper: Parse RelayState from Base64


  router.post(
    "/signIn/callback",
    passport.authenticate("saml", { failureRedirect: "/" }),
    async (req: AuthSessionRequest, res: Response, next: NextFunction): Promise<void> => {
      try {
        const samlProfile = req.user;
        if (!samlProfile) throw new NotFoundError("SAML profile missing");

        if (!samlProfile.orgId) {
          const defaultOrg = await Org.findOne({ isDeleted: false }).lean().exec();
          samlProfile.orgId = defaultOrg?._id?.toString();
        }

        const relayState = samlController.parseRelayState(req);
        const orgId = relayState.orgId || samlProfile.orgId;

        const verifiedEmail = samlController.getSamlEmail(samlProfile, orgId);
        if (!verifiedEmail) throw new BadRequestError("Invalid email in SAML attributes");

        samlProfile.email = verifiedEmail;

        let sessionToken = relayState.sessionToken;
        let session = sessionToken ? await sessionService.getSession(sessionToken) : null;
        let user: any = null; // Defined here to be accessible at the end
        const cm = await configurationManagerService.getConfig(config.cmBackend, SSO_AUTH_CONFIG_PATH, samlProfile, config.scopedJwtSecret);
        const userDetails = jitProvisioningService.extractSamlUserDetails(samlProfile, verifiedEmail);
        const orgAuthConfig = await OrgAuthConfig.findOne({ orgId, isDeleted: false });

        const allowedMethods = orgAuthConfig?.authSteps?.[0]?.allowedMethods || [];
        const hasSamlSso = allowedMethods.some(method => method.type === 'samlSso');

        if (!hasSamlSso) {
          return res.redirect(`${config.frontendUrl}/auth/sign-in?error=samlSso_Disabled`);
        }

        if (!session) {
          const iamToken = iamJwtGenerator(verifiedEmail, config.scopedJwtSecret);
          const iamResponse = await iamService.getUserByEmail(verifiedEmail, iamToken);

          if (iamResponse.data.message === "Account not found") {
            if (!cm.data?.enableJit) return res.redirect(`${config.frontendUrl}/auth/sign-in?error=jit_Disabled`);

            user = await jitProvisioningService.provisionUser(verifiedEmail, userDetails, orgId, "saml");
          } else {
            user = iamResponse.data;
          }

          session = await sessionService.createSession({
            userId: user._id,
            email: user.email,
            orgId,
            authConfig: orgAuthConfig?.authSteps || [],
            currentStep: 0,
          });
        } else {

          const iamToken = iamJwtGenerator(verifiedEmail, config.scopedJwtSecret);
          const iamResponse = await iamService.getUserByEmail(verifiedEmail, iamToken);
          user = iamResponse.data;

        }

        if (session?.userId === "NOT_FOUND" && user?.message === "Account not found") {
          const jitConfig = session.jitConfig as
            | Record<string, boolean>
            | undefined;


          if (!jitConfig) {
            if (session.userId === "NOT_FOUND") {

              return res.redirect(`${config.frontendUrl}/auth/sign-in?error=jit_Disabled`);
            }
          }
          user = await jitProvisioningService.provisionUser(verifiedEmail, userDetails, orgId, "saml");
        }

        if (!user) throw new NotFoundError("User not found");

        await sessionService.completeAuthentication(session);
        // Now 'user' is guaranteed to be available here
        const accessToken = await generateAuthToken(user, config.jwtSecret);
        const refreshToken = refreshTokenJwtGenerator(user._id, session.orgId, config.scopedJwtSecret);

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


        res.redirect(`${config.frontendUrl}/auth/sign-in/samlSso/success`);
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
