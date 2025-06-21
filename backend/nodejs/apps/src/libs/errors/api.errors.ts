import { BaseError, ErrorMetadata } from './base.error';

export class APIError extends BaseError {
  constructor(
    code: string,
    message: string,
    statusCode = 500,
    metadata?: ErrorMetadata,
  ) {
    super(`API_${code}`, message, statusCode, metadata);
  }
}

export class APIRequestError extends APIError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('REQUEST_ERROR', message, 400, metadata);
  }
}

export class APIResponseError extends APIError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('RESPONSE_ERROR', message, 500, metadata);
  }
}

export class APITimeoutError extends APIError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('TIMEOUT', message, 504, metadata);
  }
}
