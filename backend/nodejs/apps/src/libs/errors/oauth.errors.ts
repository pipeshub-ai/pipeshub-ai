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
