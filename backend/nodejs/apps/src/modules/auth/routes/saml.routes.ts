import { Router, Response, NextFunction } from 'express';
import { Container } from 'inversify';

import passport from 'passport';
import { z } from 'zod';
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
import {
  SamlDesktopHandoffService,
  isValidCodeChallenge,
  isValidDesktopState,
} from '../services/samlDesktopHandoff.service';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import {
  SAML_LOGOUT_UNSUPPORTED_MESSAGE,
  SamlController,
} from '../controller/saml.controller';
import { Logger } from '../../../libs/services/logger.service';
import { generateAuthToken } from '../utils/generateAuthToken';
import { recordEvent } from '../../../libs/services/telemetry/event-buffer';
import { AppConfig, loadAppConfig } from '../../tokens_manager/config/config';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AuthenticatedServiceRequest } from '../../../libs/middlewares/types';
import {
  SIGN_IN_ACCOUNT_CHANGED,
  UserAccountController,
} from '../controller/userAccount.controller';
import { MailService } from '../services/mail.service';
import { ConfigurationManagerService, SSO_AUTH_CONFIG_PATH } from '../services/cm.service';
import { JitProvisioningService } from '../services/jit-provisioning.service';
import {
  AuthMethodType,
  OrgAuthConfig,
} from '../schema/orgAuthConfiguration.schema';
import { EntitiesEventProducer } from '../../user_management/services/entity_events.service';
import { Org } from '../../user_management/schema/org.schema';

export const isValidEmail = (email: string) => {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email); // Basic email regex
};


const desktopExchangeValidationSchema = z.object({
  body: z.object({
    code: z.string().min(1),
    codeVerifier: z.string().min(1),
  }),
  query: z.object({}),
  params: z.object({}),
  headers: z.object({}),
});

export function createSamlRouter(container: Container) {
  const router = Router();

  let config = container.get<AppConfig>('AppConfig');
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const sessionService = container.get<SessionService>('SessionService');
  const iamService = container.get<IamService>('IamService');
  const samlController = container.get<SamlController>('SamlController');
  const jitProvisioningService = container.get<JitProvisioningService>('JitProvisioningService');
  const configurationManagerService = container.get<ConfigurationManagerService>('ConfigurationManagerService');

  const samlDesktopHandoffService = container.get<SamlDesktopHandoffService>('SamlDesktopHandoffService');

  const logger = container.get<Logger>('Logger');

  /**
   * A desktop sign-in ran in the user's browser, so its outcome goes to the
   * success page, which forwards it to the app by deep link. Web sign-ins keep
   * their existing redirects.
   */
  const desktopRelayState = (req: AuthSessionRequest) => {
    const relayState = samlController.parseRelayState(req);
    return relayState.client === 'desktop' &&
      isValidDesktopState(relayState.state) &&
      isValidCodeChallenge(relayState.codeChallenge)
      ? { state: relayState.state as string, codeChallenge: relayState.codeChallenge as string }
      : null;
  };
  const desktopSuccessUrl = (params: Record<string, string>) =>
    `${config.frontendUrl}/auth/sign-in/samlSso/success?${new URLSearchParams(params).toString()}`;
  const samlErrorUrl = (req: AuthSessionRequest, code: string) => {
    const desktop = desktopRelayState(req);
    return desktop
      ? desktopSuccessUrl({ state: desktop.state, saml_error: code })
      : `${config.frontendUrl}/login?saml_error=${encodeURIComponent(code)}`;
  };
  const redirectSamlError = (req: AuthSessionRequest, res: Response, code: string) =>
    res.redirect(samlErrorUrl(req, code));

  router.use(attachContainerMiddleware(container));
  // No server-side login session: sign-in state travels in RelayState and the
  // Redis sign-in session, and the callback sets its own token cookies.
  router.use(passport.initialize());

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
    (req: AuthSessionRequest, res: Response, next: NextFunction) => {
      try {
        // Override next so that if passport calls next(err) directly (e.g. unknown
        // strategy, SAML parse failure before the custom callback fires) we still
        // redirect to /login instead of hitting the global error handler.
        const samlErrorNext = (err?: any) => {
          if (res.headersSent) {
            logger.warn('SAML callback continued after its response was sent', {
              error: err ? err?.message || String(err) : undefined,
            });
            return;
          }
          if (err) {
            logger.error('SAML passport middleware error', { error: err?.message || String(err) });
            return redirectSamlError(req, res, err?.message || String(err));
          }
          next();
        };
        const body = req.body as Record<string, unknown> | undefined;
        if (body?.SAMLRequest !== undefined || req.query.SAMLRequest !== undefined) {
          logger.warn('Refused a SAML logout request sent to the sign-in callback');
          redirectSamlError(req, res, SAML_LOGOUT_UNSUPPORTED_MESSAGE);
          return;
        }
        passport.authenticate('saml', {
          session: false,
          failureRedirect: samlErrorUrl(req, 'auth_failed'),
        })(req, res, samlErrorNext);
      } catch (error) {
        logger.error('SAML passport error', { error: error instanceof Error ? error.message : String(error) });
        return redirectSamlError(req, res, 'auth_failed');
      }
    },
    async (req: AuthSessionRequest, res: Response, _next: NextFunction): Promise<void> => {
      try {
        const samlProfile = req.user;
        if (!samlProfile) throw new NotFoundError("SAML profile missing");

        if (!samlProfile.orgId) {
          const defaultOrg = await Org.findOne({ isDeleted: false }).lean().exec();
          samlProfile.orgId = defaultOrg?._id?.toString();
        }

        const relayState = samlController.parseRelayState(req);
        const orgId = relayState.orgId || samlProfile.orgId;

        const orgAuthConfig = await OrgAuthConfig.findOne({ orgId, isDeleted: false });
        const samlAllowed = orgAuthConfig?.authSteps?.some((step) =>
          step.allowedMethods?.some((m) => m.type === AuthMethodType.SAML_SSO),
        );
        if (!samlAllowed) {
          return redirectSamlError(req, res, 'saml_sso_disabled');
        }

        const verifiedEmail = samlController.getSamlEmail(samlProfile, orgId);
        if (!verifiedEmail) throw new BadRequestError("Invalid email in SAML attributes");

        samlProfile.email = verifiedEmail;

        let sessionToken = relayState.sessionToken;
        let session = sessionToken ? await sessionService.getSession(sessionToken) : null;
        let user: any = null; // Defined here to be accessible at the end
        const cm = await configurationManagerService.getConfig(config.cmBackend, SSO_AUTH_CONFIG_PATH, samlProfile, config.scopedJwtSecret);
        const userDetails = jitProvisioningService.extractSamlUserDetails(samlProfile, verifiedEmail);

        if (!session) {
          const iamToken = iamJwtGenerator(verifiedEmail, config.scopedJwtSecret);
          const iamResponse = await iamService.getUserByEmail(verifiedEmail, iamToken);

          if (iamResponse.statusCode === 404) {
            if (!cm.data?.enableJit) return redirectSamlError(req, res, 'jit_disabled');

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
          user = iamResponse.statusCode === 200 ? iamResponse.data : null;

          // An earlier step already proved an account (session.userId); SAML must
          // prove the same one, and before any JIT create below.
          const samlUserId: unknown = (user as { _id?: unknown } | null)?._id;
          const samlAccountId =
            typeof samlUserId === 'string' ? samlUserId : '';
          if (
            Number(session.currentStep) > 0 &&
            samlAccountId !== session.userId
          ) {
            logger.warn('SAML account differs from the earlier sign-in step');
            redirectSamlError(req, res, SIGN_IN_ACCOUNT_CHANGED);
            return;
          }
        }

        if (session?.userId === "NOT_FOUND" && !user) {
          const jitConfig = session.jitConfig as
            | Record<string, boolean>
            | undefined;
          if (!jitConfig?.saml) {
            return redirectSamlError(req, res, 'jit_disabled');
          }
          user = await jitProvisioningService.provisionUser(verifiedEmail, userDetails, orgId, "saml");
        }

        if (!user) throw new NotFoundError("User not found");

        await sessionService.completeAuthentication(session);
        // Now 'user' is guaranteed to be available here
        const accessToken = await generateAuthToken(user, config.jwtSecret);
        const refreshToken = refreshTokenJwtGenerator(user._id, session.orgId, config.scopedJwtSecret);

        recordEvent('login', {
          orgId: session.orgId?.toString(),
          userId: user._id?.toString(),
          email: user.email,
          first_login: !user.hasLoggedIn,
          auth_method: 'saml',
        });

        const desktop = desktopRelayState(req);
        if (desktop) {
          const code = await samlDesktopHandoffService.issue(
            { accessToken, refreshToken },
            desktop.codeChallenge,
          );
          return res.redirect(desktopSuccessUrl({ state: desktop.state, code }));
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


        res.redirect(`${config.frontendUrl}/auth/sign-in/samlSso/success`);
      } catch (error) {
        logger.error('SAML callback error', { error: error instanceof Error ? error.message : String(error) });
        return redirectSamlError(req, res, 'unknown');
      }
    }
  );

  // Unauthenticated by design: the code and PKCE verifier are the credential.
  router.post(
    '/desktop/exchange',
    ValidationMiddleware.validate(desktopExchangeValidationSchema),
    async (req: AuthSessionRequest, res: Response, next: NextFunction) => {
      try {
        const { code, codeVerifier } = req.body as { code: string; codeVerifier: string };
        res.status(200).json(await samlDesktopHandoffService.redeem(code, codeVerifier));
      } catch (error) {
        next(error);
      }
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
              container.get<JitProvisioningService>('JitProvisioningService'),
              container.get<EntitiesEventProducer>('EntitiesEventProducer'),
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
        });
        return;
      } catch (error) {
        next(error);
      }
    },
  );
  return router;
}
