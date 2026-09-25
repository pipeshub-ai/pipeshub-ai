import {
  BadRequestError,
  ForbiddenError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
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

export const SIGN_IN_METHOD_NOT_ALLOWED =
  "That sign-in method isn't turned on for this step. Go back to the sign-in page and use one of the options it shows, or ask your admin which sign-in methods are enabled.";

/**
 * Throws unless `method` is one of the methods the org allows at the sign-in
 * step the session is on. Checking against every step would let a user repeat
 * step one's method at step two and skip the second factor.
 */
export function assertMethodAllowedAtStep(
  authSteps: IOrgAuthConfigLike['authSteps'] | undefined,
  currentStep: number,
  method: string,
): void {
  const allowed = authSteps?.[currentStep]?.allowedMethods ?? [];
  if (!allowed.some((m) => m.type === method)) {
    throw new BadRequestError(SIGN_IN_METHOD_NOT_ALLOWED);
  }
}
