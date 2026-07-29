import { ForbiddenError, NotFoundError } from '../../../libs/errors/http.errors';
import { AuthMethodType } from '../schema/orgAuthConfiguration.schema';

export interface IOrgAuthConfigLike {
  authSteps: Array<{
    allowedMethods: Array<{ type: string }>;
  }>;
}

/**
 * Throws if the requested auth method is not enabled in the org's auth config.
 * Call this before performing any credential verification so that an org admin's
 * settings are always enforced, regardless of which method the client requests.
 */
export function assertAuthMethodEnabled(
  orgAuthConfig: IOrgAuthConfigLike | null | undefined,
  method: AuthMethodType,
): void {
  if (!orgAuthConfig) {
    throw new NotFoundError('Auth configuration not found for this organization');
  }

  const allowed = orgAuthConfig.authSteps.some((step) =>
    step.allowedMethods.some((m) => m.type === method),
  );

  if (!allowed) {
    throw new ForbiddenError(
      `"${method}" authentication is not enabled for this organization`,
    );
  }
}
