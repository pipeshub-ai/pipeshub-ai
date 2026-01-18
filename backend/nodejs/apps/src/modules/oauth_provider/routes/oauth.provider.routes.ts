import { Router, Request, Response, NextFunction } from 'express'
import { Container } from 'inversify'
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware'
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware'
import { OAuthProviderController } from '../controller/oauth.provider.controller'
import { OIDCProviderController } from '../controller/oid.provider.controller'
import {
  authorizeQuerySchema,
  authorizeConsentSchema,
  tokenSchema,
  revokeSchema,
  introspectSchema,
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

  /**
   * GET /authorize
   * Authorization endpoint - initiates OAuth flow
   * Requires user to be logged in
   */
  router.get(
    '/authorize',
    authMiddleware.authenticate.bind(authMiddleware),
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
   */
  router.post(
    '/token',
    ValidationMiddleware.validate(tokenSchema),
    (req: Request, res: Response, next: NextFunction) =>
      controller.token(req, res, next),
  )

  /**
   * POST /revoke
   * Revocation endpoint - revokes access or refresh tokens
   */
  router.post(
    '/revoke',
    ValidationMiddleware.validate(revokeSchema),
    (req: Request, res: Response, next: NextFunction) =>
      controller.revoke(req, res, next),
  )

  /**
   * POST /introspect
   * Token introspection endpoint
   */
  router.post(
    '/introspect',
    ValidationMiddleware.validate(introspectSchema),
    (req: Request, res: Response, next: NextFunction) =>
      controller.introspect(req, res, next),
  )

  /**
   * GET /userinfo
   * OpenID Connect UserInfo endpoint
   * Bearer token authentication
   */
  router.get(
    '/userinfo',
    (req: Request, res: Response, next: NextFunction) =>
      oidcController.userInfo(req, res, next),
  )

  return router
}
