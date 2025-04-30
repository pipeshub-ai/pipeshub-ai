import { HTTP_STATUS } from '../enums/http-status.enum';
import { BaseError, ErrorMetadata } from './base.error';

export class HttpError extends BaseError {
  constructor(
    code: string,
    message: string,
    statusCode = 500,
    metadata?: ErrorMetadata,
  ) {
    super(`HTTP_${code}`, message, statusCode, metadata);
  }
}

export class BadRequestError extends HttpError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('BAD_REQUEST', message, HTTP_STATUS.BAD_REQUEST, metadata);
  }
}

export class UnauthorizedError extends HttpError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('UNAUTHORIZED', message, HTTP_STATUS.UNAUTHORIZED, metadata);
  }
}
export class GoneError extends HttpError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('GONE', message, HTTP_STATUS.GONE, metadata);
  }
}
export class LargePayloadError extends HttpError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super(
      'PAYLOAD_TOO_LARGE',
      message,
      HTTP_STATUS.PAYLOAD_TOO_LARGE,
      metadata,
    );
  }
}

export class ForbiddenError extends HttpError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('FORBIDDEN', message, HTTP_STATUS.FORBIDDEN, metadata);
  }
}

export class NotFoundError extends HttpError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('NOT_FOUND', message, HTTP_STATUS.NOT_FOUND, metadata);
  }
}

export class ConflictError extends HttpError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('CONFLICT', message, HTTP_STATUS.CONFLICT, metadata);
  }
}

export class TooManyRequestsError extends HttpError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super(
      'TOO_MANY_REQUESTS',
      message,
      HTTP_STATUS.TOO_MANY_REQUESTS,
      metadata,
    );
  }
}

export class InternalServerError extends HttpError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super(
      'INTERNAL_SERVER_ERROR',
      message,
      HTTP_STATUS.INTERNAL_SERVER,
      metadata,
    );
  }
}

export class ServiceUnavailableError extends HttpError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super(
      'SERVICE_UNAVAILABLE',
      message,
      HTTP_STATUS.SERVICE_UNAVAILABLE,
      metadata,
    );
  }
}

export class BadGatewayError extends HttpError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('BAD_GATEWAY', message, HTTP_STATUS.BAD_GATEWAY, metadata);
  }
}

export class GatewayTimeoutError extends HttpError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('GATEWAY_TIMEOUT', message, HTTP_STATUS.GATEWAY_TIMEOUT, metadata);
  }
}

export class UnprocessableEntityError extends HttpError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super(
      'UNPROCESSABLE_ENTITY',
      message,
      HTTP_STATUS.UNPROCESSABLE_ENTITY,
      metadata,
    );
  }
}

export class NotImplementedError extends HttpError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('NOT_IMPLEMENTED', message, HTTP_STATUS.NOT_IMPLEMENTED, metadata);
  }
}
