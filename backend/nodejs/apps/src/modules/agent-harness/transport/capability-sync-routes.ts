import { RequestHandler, Router } from 'express';
import { Container } from 'inversify';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import {
  createCapabilitySyncRouter,
  type CapabilitySync,
} from '../../control-plane';
import { boundOrNull } from '../binders/context';
import { CONTROL_PLANE_CAPABILITY_SYNC } from '../binders/tokens';

export { CAPABILITY_SYNC_PATH } from '../../control-plane';

/**
 * CPD-7's dual write, Node side: the query service's `capabilities:sync`
 * service token only, with the capability migration the harness binder made
 * (503 where it made none).
 */
export function createHarnessCapabilitySyncRouter(
  container: Container,
): Router {
  // Resolved per request: some processes build their routers before the host binds its auth.
  const authorize: RequestHandler = (req, res, next) =>
    container
      .get<AuthMiddleware>('AuthMiddleware')
      .scopedTokenValidator(TokenScopes.CAPABILITIES_SYNC)(req, res, next);
  return createCapabilitySyncRouter({
    authorize,
    migration: () =>
      boundOrNull<CapabilitySync>(container, CONTROL_PLANE_CAPABILITY_SYNC),
  });
}
