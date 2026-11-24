import { Response } from 'express';
import { BaseError } from '../../../libs/errors/base.error';
import {
  BadRequestError,
  ForbiddenError,
  NotFoundError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import {
  AccessDeniedError,
  DeviceGrantError,
  ExpiredTokenError,
  InvalidClientError,
  InvalidGrantError,
  InvalidScopeError,
  InvalidTargetError,
  InvalidTokenError,
  OAuthError,
  TokenExchangeError,
  UnauthorizedClientError,
  UnsupportedGrantTypeError,
} from '../../../libs/errors/oauth.errors';
import { markRequestError } from '../../../libs/middlewares/access-log.middleware';
import { Logger } from '../../../libs/services/logger.service';
import {
  classifyRequestError,
  levelFor,
} from '../../../libs/utils/error-class';
import { OAuthErrorResponse } from '../types/oauth.types';

/** What a client is told when we, not it, failed: our own text never reaches a third party. */
export const OAUTH_SERVER_FAILURE =
  'The authorization server could not complete the request';

/**
 * The HTTP refusals the grant code raises on purpose. Not every HttpError: the
 * Postgres seam turns a lock timeout into a ConflictError (409), which is ours.
 */
const DELIBERATE_REFUSALS = [
  BadRequestError,
  NotFoundError,
  UnauthorizedError,
  ForbiddenError,
] as const;

/**
 * The client's fault: an OAuth error with a 4xx status, or one of the deliberate
 * refusals. Anything else is ours, whatever its status: a DecryptionError (400)
 * after a key change, a ConflictError from a lock timeout, a driver error.
 */
export const isOAuthRefusal = (error: unknown): error is BaseError =>
  (error instanceof OAuthError && error.statusCode < 500) ||
  DELIBERATE_REFUSALS.some((type) => error instanceof type);

/** The RFC 6749 §5.2 / §4.1.2.1 code of a refusal; never `server_error`, which is ours only. */
export const oauthErrorCode = (error: BaseError): string => {
  if (
    error instanceof DeviceGrantError ||
    error instanceof TokenExchangeError
  ) {
    return error.oauthError;
  }
  if (error instanceof InvalidClientError) return 'invalid_client';
  if (error instanceof UnauthorizedClientError) return 'unauthorized_client';
  if (error instanceof UnsupportedGrantTypeError)
    return 'unsupported_grant_type';
  if (error instanceof InvalidScopeError) return 'invalid_scope';
  if (error instanceof InvalidTargetError) return 'invalid_target';
  if (error instanceof AccessDeniedError) return 'access_denied';
  // A dead, rotated or unknown refresh token: clients drop it only on invalid_grant.
  if (
    error instanceof InvalidGrantError ||
    error instanceof InvalidTokenError ||
    error instanceof ExpiredTokenError ||
    error instanceof NotFoundError ||
    error instanceof UnauthorizedError
  ) {
    return 'invalid_grant';
  }
  return 'invalid_request';
};

/** The RFC 6749 body of a refusal. */
export const oauthErrorBody = (
  error: BaseError,
  state?: string,
): OAuthErrorResponse => ({
  error: oauthErrorCode(error),
  error_description: error.message,
  ...(state !== undefined && state !== '' && { state }),
});

/**
 * The one line for our own failure of an OAuth endpoint, and the access line's
 * class. The class is the one a 500 would have: an endpoint that answers 503 so
 * the client retries must not turn our bug into an outage.
 */
export const logOAuthServerFailure = (
  logger: Logger,
  res: Response,
  error: unknown,
): void => {
  const errorClass = classifyRequestError(error, 500);
  markRequestError(res, errorClass);
  logger[levelFor(errorClass)]('oauth.request.failed', { error });
};

/** Answers our own failure: 500 `server_error`, or 503 `temporarily_unavailable` where the RFC says retry. */
export const answerOAuthServerFailure = (
  logger: Logger,
  res: Response,
  error: unknown,
  status: 500 | 503 = 500,
): void => {
  logOAuthServerFailure(logger, res, error);
  if (res.headersSent) return;
  res.status(status).json({
    error: status === 503 ? 'temporarily_unavailable' : 'server_error',
    error_description: OAUTH_SERVER_FAILURE,
  });
};
