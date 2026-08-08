import { Router } from 'express'
import { Container } from 'inversify'
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware'
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware'
import { createOAuthClientRateLimiter } from '../../../libs/middlewares/rate-limit.middleware'
import { Logger } from '../../../libs/services/logger.service'
import { PatController } from '../controller/pat.controller'
import { AppConfig } from '../../tokens_manager/config/config'
import { createPatTokenSchema, tokenIdParamsSchema } from '../validators/pat.validators'

export function createPatRouter(container: Container): Router {
  const router = Router()
  const controller = container.get<PatController>('PatController')
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware')
  const logger = container.get<Logger>('Logger')
  const appConfig = container.get<AppConfig>('AppConfig')

  // Reuses the OAuth-client management rate limiter/budget for v1 — PAT
  // create/list/revoke traffic is the same low-volume, user-driven shape.
  const patRateLimiter = createOAuthClientRateLimiter(
    logger,
    appConfig.maxOAuthClientRequestsPerMinute,
  )

  // All routes require authentication — non-admins mint their own tokens.
  router.use(authMiddleware.authenticate.bind(authMiddleware))
  router.use(patRateLimiter)

  /**
   * GET /personal-access-tokens
   * List the calling user's own personal access tokens
   */
  router.get('/', (req, res, next) => controller.listTokens(req, res, next))

  /**
   * POST /personal-access-tokens
   * Create a new personal access token
   */
  router.post(
    '/',
    ValidationMiddleware.validate(createPatTokenSchema),
    (req, res, next) => controller.createToken(req, res, next),
  )

  /**
   * GET /personal-access-tokens/scopes
   * List the scopes available to a new personal access token
   */
  router.get('/scopes', (req, res, next) => controller.listScopes(req, res, next))

  /**
   * DELETE /personal-access-tokens/:tokenId
   * Revoke one of the calling user's own personal access tokens
   */
  router.delete(
    '/:tokenId',
    ValidationMiddleware.validate(tokenIdParamsSchema),
    (req, res, next) => controller.revokeToken(req, res, next),
  )

  return router
}
