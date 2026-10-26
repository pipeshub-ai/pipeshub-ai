import { BaseError, ErrorMetadata } from './base.error';

export class OAuthError extends BaseError {
  constructor(
    code: string,
    message: string,
    statusCode = 500,
    metadata?: ErrorMetadata,
  ) {
    super(`OAUTH_${code}`, message, statusCode, metadata);
  }
}

export class InvalidGrantError extends OAuthError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('INVALID_GRANT', message, 400, metadata);
  }
}

export class InvalidTokenError extends OAuthError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('INVALID_TOKEN', message, 401, metadata);
  }
}

export class ExpiredTokenError extends OAuthError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('EXPIRED_TOKEN', message, 401, metadata);
  }
}

export class InvalidScopeError extends OAuthError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('INVALID_SCOPE', message, 400, metadata);
  }
}

export class AuthorizationError extends OAuthError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('AUTHORIZATION_ERROR', message, 403, metadata);
  }
}

export class InvalidClientError extends OAuthError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('INVALID_CLIENT', message, 401, metadata);
  }
}

export class InvalidRedirectUriError extends OAuthError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('INVALID_REDIRECT_URI', message, 400, metadata);
  }
}

export class UnsupportedGrantTypeError extends OAuthError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('UNSUPPORTED_GRANT_TYPE', message, 400, metadata);
  }
}

export class AccessDeniedError extends OAuthError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('ACCESS_DENIED', message, 403, metadata);
  }
}

export class UnauthorizedClientError extends OAuthError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('UNAUTHORIZED_CLIENT', message, 401, metadata);
  }
}

export class ServerError extends OAuthError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('SERVER_ERROR', message, 500, metadata);
  }
}

/**
 * RFC 8628 device-grant errors. Always HTTP 400; the JSON `error` member is
 * `oauthError` (`authorization_pending`, `slow_down`, `expired_token`, …).
 */
export class DeviceGrantError extends OAuthError {
  readonly oauthError: string

  constructor(oauthError: string, message: string, metadata?: ErrorMetadata) {
    super(oauthError.toUpperCase().replace(/-/g, '_'), message, 400, metadata)
    this.oauthError = oauthError
  }
}

/**
 * RFC 8693 token-exchange errors. Always HTTP 400; the JSON `error` member is
 * `oauthError` (`invalid_target`, `invalid_request`, `invalid_grant`).
 *
 * `invalid_target` is deliberately returned for every refusal about the
 * person being asked for — no such user, not on the allow-list, an
 * administrator, a disabled account — and always with the same description.
 * A service token that could tell those apart would be a working directory of
 * the organisation and a map of its administrators, readable by anything that
 * got hold of the credential. The real cause is written to the delegation
 * audit log, which only an administrator can read.
 */
export class TokenExchangeError extends OAuthError {
  readonly oauthError: string;

  constructor(oauthError: string, message: string, metadata?: ErrorMetadata) {
    super(oauthError.toUpperCase().replace(/-/g, '_'), message, 400, metadata);
    this.oauthError = oauthError;
  }
}
