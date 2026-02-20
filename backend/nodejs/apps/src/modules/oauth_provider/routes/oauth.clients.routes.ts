import { Router } from 'express'
import { Container } from 'inversify'
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware'
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware'
import { createOAuthClientRateLimiter } from '../../../libs/middlewares/rate-limit.middleware'
import { Logger } from '../../../libs/services/logger.service'
import { OAuthAppController } from '../controller/oauth.app.controller'
import { userAdminCheck } from '../../user_management/middlewares/userAdminCheck'
import {
  appIdParamsSchema,
  createAppSchema,
  updateAppSchema,
  listAppsQuerySchema,
} from '../validators/oauth.validators'

export function createOAuthClientsRouter(container: Container): Router {
  const router = Router()
  const controller = container.get<OAuthAppController>('OAuthAppController')
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware')
  const logger = container.get<Logger>('Logger')

  // Rate limiter for OAuth client management (10 requests per minute)
  const oauthClientRateLimiter = createOAuthClientRateLimiter(logger)

  // All routes require authentication
  router.use(authMiddleware.authenticate.bind(authMiddleware))
  // All routes require admin auth
  router.use(userAdminCheck)
  // All routes must be rate limited
  router.use(oauthClientRateLimiter)

  /**
   * GET /oauth-clients
   * List all OAuth apps for the organization
   */
  router.get(
    '/',
    ValidationMiddleware.validate(listAppsQuerySchema),
    (req, res, next) => controller.listApps(req, res, next),
  )

  /**
   * POST /oauth-clients
   * Create a new OAuth app
   * Admin only, rate limited
   */
  router.post(
    '/',
    ValidationMiddleware.validate(createAppSchema),
    (req, res, next) => controller.createApp(req, res, next),
  )

  /**
   * GET /oauth-clients/scopes
   * List all available scopes
   */
  router.get(
    '/scopes',
    (req, res, next) => controller.listScopes(req, res, next),
  )

  /**
   * GET /oauth-clients/:appId
   * Get OAuth app details
   */
  router.get(
    '/:appId',
    ValidationMiddleware.validate(appIdParamsSchema),
    (req, res, next) => controller.getApp(req, res, next),
  )

  /**
   * PUT /oauth-clients/:appId
   * Update OAuth app
   * Admin only, rate limited
   */
  router.put(
    '/:appId',
    ValidationMiddleware.validate(updateAppSchema),
    (req, res, next) => controller.updateApp(req, res, next),
  )

  /**
   * DELETE /oauth-clients/:appId
   * Delete OAuth app (soft delete)
   * Admin only, rate limited
   */
  router.delete(
    '/:appId',
    ValidationMiddleware.validate(appIdParamsSchema),
    (req, res, next) => controller.deleteApp(req, res, next),
  )

  /**
   * POST /oauth-clients/:appId/regenerate-secret
   * Regenerate client secret
   */
  router.post(
    '/:appId/regenerate-secret',
    ValidationMiddleware.validate(appIdParamsSchema),
    (req, res, next) => controller.regenerateSecret(req, res, next),
  )

  /**
   * POST /oauth-clients/:appId/suspend
   * Suspend an OAuth app
   */
  router.post(
    '/:appId/suspend',
    ValidationMiddleware.validate(appIdParamsSchema),
    (req, res, next) => controller.suspendApp(req, res, next),
  )

  /**
   * POST /oauth-clients/:appId/activate
   * Reactivate a suspended OAuth app
   */
  router.post(
    '/:appId/activate',
    ValidationMiddleware.validate(appIdParamsSchema),
    (req, res, next) => controller.activateApp(req, res, next),
  )

  /**
   * GET /oauth-clients/:appId/tokens
   * List active tokens for an app
   */
  router.get(
    '/:appId/tokens',
    ValidationMiddleware.validate(appIdParamsSchema),
    (req, res, next) => controller.listAppTokens(req, res, next),
  )

  /**
   * POST /oauth-clients/:appId/revoke-all-tokens
   * Revoke all tokens for an app
   */
  router.post(
    '/:appId/revoke-all-tokens',
    ValidationMiddleware.validate(appIdParamsSchema),
    (req, res, next) => controller.revokeAllTokens(req, res, next),
  )

  return router
}
