/**
 * OAuth Dynamic Client Registration routes (RFC 7591) and Client
 * Configuration routes (RFC 7592).
 *
 * Mounted under the same `/api/v1/oauth2` prefix as the rest of the OAuth
 * provider — so the registration endpoint surfaces as
 * `https://<host>/api/v1/oauth2/register`, which is what
 * `/.well-known/oauth-authorization-server` advertises.
 */
import { Router, Request, Response, NextFunction } from 'express'
import { Container } from 'inversify'
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware'
import {
  createOAuthClientRateLimiter,
  createDcrRegisterRateLimiter,
} from '../../../libs/middlewares/rate-limit.middleware'
import { Logger } from '../../../libs/services/logger.service'
import { AppConfig } from '../../tokens_manager/config/config'
import { OAuthDcrController } from '../controller/oauth.dcr.controller'
import {
  OAuthRegistrationTokenMiddleware,
  RegistrationManagementRequest,
} from '../middlewares/oauth.registration_token.middleware'
import {
  registerBodySchema,
  registrationManagementGetSchema,
  registrationManagementPutSchema,
  registrationManagementDeleteSchema,
} from '../validators/oauth.dcr.validators'

export function createOAuthDcrRouter(container: Container): Router {
  const router = Router()
  const controller = container.get<OAuthDcrController>('OAuthDcrController')
  const registrationTokenMiddleware =
    container.get<OAuthRegistrationTokenMiddleware>(
      'OAuthRegistrationTokenMiddleware',
    )
  const logger = container.get<Logger>('Logger')
  const appConfig = container.get<AppConfig>('AppConfig')

  // Generic OAuth-client limiter for the auth-gated RFC 7592 management routes.
  const dcrMgmtRateLimiter = createOAuthClientRateLimiter(
    logger,
    appConfig.maxOAuthClientRequestsPerMinute,
  )

  // Dedicated, much stricter per-IP limiter for the anonymous register endpoint.
  // Each successful call writes a new oauthApps row + mints credentials, so we
  // intentionally cap this at a small fraction of the general OAuth client cap.
  const dcrRegisterRateLimiter = createDcrRegisterRateLimiter(
    logger,
    appConfig.maxDcrRegistrationsPerMinute,
  )

  /**
   * POST /register
   *
   * RFC 7591 §3.1. Anonymous (no auth header). Strict per-IP rate limit to
   * deter spammy registrations. Returns 201 with the full registration
   * response including freshly-issued client_secret (confidential clients
   * only) and registration_access_token.
   */
  router.post(
    '/register',
    dcrRegisterRateLimiter,
    ValidationMiddleware.validate(registerBodySchema),
    (req: Request, res: Response, next: NextFunction) =>
      controller.register(req, res, next),
  )

  /**
   * GET /register/:client_id
   *
   * RFC 7592 §2.1. Bearer auth via registration_access_token.
   */
  router.get(
    '/register/:client_id',
    ValidationMiddleware.validate(registrationManagementGetSchema),
    registrationTokenMiddleware.authenticate,
    (req: Request, res: Response, next: NextFunction) =>
      controller.getRegistration(
        req as RegistrationManagementRequest,
        res,
        next,
      ),
  )

  /**
   * PUT /register/:client_id
   *
   * RFC 7592 §2.2. Replaces the client metadata. Bearer auth required.
   */
  router.put(
    '/register/:client_id',
    ValidationMiddleware.validate(registrationManagementPutSchema),
    registrationTokenMiddleware.authenticate,
    dcrMgmtRateLimiter,
    (req: Request, res: Response, next: NextFunction) =>
      controller.updateRegistration(
        req as RegistrationManagementRequest,
        res,
        next,
      ),
  )

  /**
   * DELETE /register/:client_id
   *
   * RFC 7592 §2.3. Soft-deletes the client and revokes every outstanding
   * token issued under it. Bearer auth required.
   */
  router.delete(
    '/register/:client_id',
    ValidationMiddleware.validate(registrationManagementDeleteSchema),
    registrationTokenMiddleware.authenticate,
    dcrMgmtRateLimiter,
    (req: Request, res: Response, next: NextFunction) =>
      controller.deleteRegistration(
        req as RegistrationManagementRequest,
        res,
        next,
      ),
  )

  return router
}
