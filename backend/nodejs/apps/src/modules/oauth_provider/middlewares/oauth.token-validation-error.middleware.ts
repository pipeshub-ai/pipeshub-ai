import { Request, Response, NextFunction } from 'express';
import { ValidationError } from '../../../libs/errors/validation.error';
import { OAuthErrorResponse } from '../types/oauth.types';

/**
 * Token endpoint errors use the RFC 6749 section 5.2 body, which OAuth clients
 * parse, so a request the schema rejects must not fall through to the generic
 * `{ code, message }` error handler.
 */
export function oauthTokenValidationErrorHandler(
  err: unknown,
  req: Request,
  res: Response,
  next: NextFunction,
): void {
  if (!(err instanceof ValidationError)) {
    next(err);
    return;
  }

  const grantType = (req.body as { grant_type?: unknown } | undefined)
    ?.grant_type;
  const grantTypeRejected = err.errors.some(
    (e) => e.field === 'body.grant_type',
  );
  const body: OAuthErrorResponse = {
    error:
      grantTypeRejected && typeof grantType === 'string' && grantType !== ''
        ? 'unsupported_grant_type'
        : 'invalid_request',
    error_description: err.message,
  };
  res.status(400).json(body);
}
