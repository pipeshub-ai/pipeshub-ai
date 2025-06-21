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
