import { Router, Request, Response, NextFunction } from 'express'
import { Container } from 'inversify'
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware'
import { AuthMiddleware } from '../../../config'
import { createOAuthClientRateLimiter } from '../../../libs/middlewares/rate-limit.middleware'
import { OAuthProviderController } from '../controller/oauth.provider.controller'
import { OIDCProviderController } from '../controller/oid.provider.controller'
import { Logger } from '../../../libs/services/logger.service'
import { AuthTokenService } from '../../../libs/services/authtoken.service'
import { createOAuthRedirectMiddleware } from '../middlewares/oauth.redirect.middleware'
import { OAuthAuthMiddleware } from '../middlewares/oauth.auth.middleware'
import { AppConfig } from '../../tokens_manager/config/config'
import {
  authorizeQuerySchema,
  authorizeConsentSchema,
  tokenSchema,
  revokeSchema,
  introspectSchema,
  dcrRequestSchema,
  deviceAuthorizationSchema,
  deviceUserCodeSchema,
} from '../validators/oauth.validators'

export function createOAuthProviderRouter(container: Container): Router {
  const router = Router()
  const controller = container.get<OAuthProviderController>(
    'OAuthProviderController',
  )
  const oidcController = container.get<OIDCProviderController>(
    'OIDCProviderController',
  )
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware')
  const oauthAuthMiddleware = container.get<OAuthAuthMiddleware>('OAuthAuthMiddleware')
  const logger = container.get<Logger>('Logger')
  const authTokenService = container.get<AuthTokenService>('AuthTokenService')
  const appConfig = container.get<AppConfig>('AppConfig')

  // Frontend URL for OAuth redirects (defaults to localhost:3001)
  const frontendUrl = appConfig.frontendUrl

  // Create OAuth redirect middleware for browser-based OAuth flow
  const oauthRedirectMiddleware = createOAuthRedirectMiddleware(
    authTokenService,
    logger,
    frontendUrl,
  )

  // RFC 9700 / RFC 7662: Rate limit token, revocation, and introspection endpoints
  const oauthTokenRateLimiter = createOAuthClientRateLimiter(logger, appConfig.maxOAuthClientRequestsPerMinute)

  /**
   * GET /authorize
   * Authorization endpoint - initiates OAuth flow
   * Uses redirect middleware: if user not logged in, redirects to frontend
   * instead of returning 401 (since browser requests don't have JWT in header)
   */
  router.get(
    '/authorize',
    oauthRedirectMiddleware,
    ValidationMiddleware.validate(authorizeQuerySchema),
    (req: Request, res: Response, next: NextFunction) =>
      controller.authorize(req as Parameters<typeof controller.authorize>[0], res, next),
  )

  /**
   * POST /authorize
   * User consent submission
   */
  router.post(
    '/authorize',
    authMiddleware.authenticate.bind(authMiddleware),
    ValidationMiddleware.validate(authorizeConsentSchema),
    (req: Request, res: Response, next: NextFunction) =>
      controller.authorizeConsent(req as Parameters<typeof controller.authorizeConsent>[0], res, next),
  )

  /**
   * POST /token
   * Token endpoint - exchanges auth code or credentials for tokens
   * No authentication required (client authenticates via credentials)
   * Rate limited
   */
  router.post(
    '/token',
    oauthTokenRateLimiter,
    ValidationMiddleware.validate(tokenSchema),
    (req: Request, res: Response, next: NextFunction) =>
      controller.token(req, res, next),
  )

  /**
   * POST /revoke
   * Revocation endpoint - revokes access or refresh tokens
   * Rate limited
   */
  router.post(
    '/revoke',
    oauthTokenRateLimiter,
    ValidationMiddleware.validate(revokeSchema),
    (req: Request, res: Response, next: NextFunction) =>
      controller.revoke(req, res, next),
  )

  /**
   * POST /introspect
   * Token introspection endpoint
   * Rate limited
   */
  router.post(
    '/introspect',
    oauthTokenRateLimiter,
    ValidationMiddleware.validate(introspectSchema),
    (req: Request, res: Response, next: NextFunction) =>
      controller.introspect(req, res, next),
  )

  /**
   * GET /userinfo
   * OpenID Connect UserInfo endpoint
   * Bearer token authentication with 'openid' scope required
   */
  router.get(
    '/userinfo',
    oauthAuthMiddleware.authenticate,
    oauthAuthMiddleware.requireScopes('openid'),
    (req: Request, res: Response, next: NextFunction) =>
      oidcController.userInfo(req, res, next),
  )

  /**
   * POST /register
   * RFC 7591 Dynamic Client Registration. Never issues client_credentials.
   */
  router.post(
    '/register',
    oauthTokenRateLimiter,
    ValidationMiddleware.validate(dcrRequestSchema),
    (req: Request, res: Response, next: NextFunction) =>
      controller.register(req, res, next),
  )

  /**
   * POST /device_authorization
   * RFC 8628 Device Authorization Grant.
   */
  router.post(
    '/device_authorization',
    oauthTokenRateLimiter,
    ValidationMiddleware.validate(deviceAuthorizationSchema),
    (req: Request, res: Response, next: NextFunction) => {
      ;(req as Request & { oauthFrontendUrl?: string }).oauthFrontendUrl =
        frontendUrl
      controller.deviceAuthorization(req, res, next)
    },
  )

  /**
   * POST /device/verify — authenticated user_code lookup for the consent page.
   */
  router.post(
    '/device/verify',
    authMiddleware.authenticate.bind(authMiddleware),
    ValidationMiddleware.validate(deviceUserCodeSchema),
    (req: Request, res: Response, next: NextFunction) =>
      controller.deviceVerify(
        req as Parameters<typeof controller.deviceVerify>[0],
        res,
        next,
      ),
  )

  /**
   * POST /device/consent — user approves or denies a device grant.
   */
  router.post(
    '/device/consent',
    authMiddleware.authenticate.bind(authMiddleware),
    ValidationMiddleware.validate(deviceUserCodeSchema),
    (req: Request, res: Response, next: NextFunction) =>
      controller.deviceConsent(
        req as Parameters<typeof controller.deviceConsent>[0],
        res,
        next,
      ),
  )

  return router
}
