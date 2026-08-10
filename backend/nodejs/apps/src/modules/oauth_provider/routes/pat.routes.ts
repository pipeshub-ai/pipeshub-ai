import { Router } from 'express'
import { Container } from 'inversify'
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware'
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware'
import { createOAuthClientRateLimiter } from '../../../libs/middlewares/rate-limit.middleware'
import { Logger } from '../../../libs/services/logger.service'
import { PatController } from '../controller/pat.controller'
import { AppConfig } from '../../tokens_manager/config/config'
import {
  createPatTokenSchema,
  listAdminPatQuerySchema,
  tokenIdParamsSchema,
} from '../validators/pat.validators'
import { userAdminCheck } from '../../user_management/middlewares/userAdminCheck'

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

  router.get('/', (req, res, next) => controller.listTokens(req, res, next))

  router.post(
    '/',
    ValidationMiddleware.validate(createPatTokenSchema),
    (req, res, next) => controller.createToken(req, res, next),
  )

  router.get('/scopes', (req, res, next) => controller.listScopes(req, res, next))

  router.delete(
    '/:tokenId',
    ValidationMiddleware.validate(tokenIdParamsSchema),
    (req, res, next) => controller.revokeToken(req, res, next),
  )

  // Admin-only: incident response on any user's token in the org (departed
  // employee, compromised laptop) — not reachable by regular members.
  router.get(
    '/admin',
    userAdminCheck,
    ValidationMiddleware.validate(listAdminPatQuerySchema),
    (req, res, next) => controller.adminListTokens(req, res, next),
  )

  router.delete(
    '/admin/:tokenId',
    userAdminCheck,
    ValidationMiddleware.validate(tokenIdParamsSchema),
    (req, res, next) => controller.adminRevokeToken(req, res, next),
  )

  return router
}
