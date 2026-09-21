import { Router } from 'express';
import { Container } from 'inversify';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { ServiceAccountsController } from '../controller/service-accounts.controller';
import { userAdminCheck } from '../middlewares/userAdminCheck';
import {
  createServiceAccountSchema,
  serviceAccountIdParamsSchema,
  updateServiceAccountSchema,
} from '../validators/service-account.validators';

export function createServiceAccountsRouter(container: Container): Router {
  const router = Router();
  const controller = container.get<ServiceAccountsController>(
    'ServiceAccountsController',
  );
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');

  router.use(authMiddleware.authenticate.bind(authMiddleware));

  // Two gates, because they answer different questions and neither covers
  // the other.
  //
  // `userAdminCheck` asks whether the person behind the request administers
  // the organisation. Creating a service account is an act of granting
  // access to the organisation's documents, so it belongs with the people
  // who administer access.
  //
  // `requireScopes` asks what the *credential* is allowed to do. It is a
  // no-op for session JWTs and is the only thing enforced for OAuth tokens
  // and personal access tokens. Without it, an administrator's narrowly
  // scoped PAT — one minted to read a knowledge base, say — could create a
  // principal with its own view of the organisation. A credential should not
  // be able to do more than it was minted for just because its owner is an
  // admin. The scopes mirror the ones on /users, since these are users.
  router.get(
    '/',
    requireScopes(OAuthScopeNames.USER_READ),
    userAdminCheck,
    (req, res, next) => controller.list(req, res, next),
  );

  router.post(
    '/',
    requireScopes(OAuthScopeNames.USER_INVITE),
    userAdminCheck,
    ValidationMiddleware.validate(createServiceAccountSchema),
    (req, res, next) => controller.create(req, res, next),
  );

  router.get(
    '/:id',
    requireScopes(OAuthScopeNames.USER_READ),
    userAdminCheck,
    ValidationMiddleware.validate(serviceAccountIdParamsSchema),
    (req, res, next) => controller.get(req, res, next),
  );

  router.patch(
    '/:id',
    requireScopes(OAuthScopeNames.USER_WRITE),
    userAdminCheck,
    ValidationMiddleware.validate(updateServiceAccountSchema),
    (req, res, next) => controller.update(req, res, next),
  );

  router.delete(
    '/:id',
    requireScopes(OAuthScopeNames.USER_DELETE),
    userAdminCheck,
    ValidationMiddleware.validate(serviceAccountIdParamsSchema),
    (req, res, next) => controller.remove(req, res, next),
  );

  return router;
}
