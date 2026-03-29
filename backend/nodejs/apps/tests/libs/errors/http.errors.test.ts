import { expect } from 'chai';
import { BaseError } from '../../../src/libs/errors/base.error';
import {
  HttpError,
  BadRequestError,
  UnauthorizedError,
  GoneError,
  LargePayloadError,
  ForbiddenError,
  NotFoundError,
  ConflictError,
  TooManyRequestsError,
  InternalServerError,
  ServiceUnavailableError,
  BadGatewayError,
  GatewayTimeoutError,
  UnprocessableEntityError,
  NotImplementedError,
} from '../../../src/libs/errors/http.errors';

describe('HTTP Errors', () => {
  describe('HttpError', () => {
    it('should have correct name', () => {
      const error = new HttpError('CUSTOM', 'HTTP error', 500);
      expect(error.name).to.equal('HttpError');
    });

    it('should have correct code with HTTP_ prefix', () => {
      const error = new HttpError('CUSTOM', 'HTTP error', 500);
      expect(error.code).to.equal('HTTP_CUSTOM');
    });

    it('should have correct statusCode', () => {
      const error = new HttpError('CUSTOM', 'HTTP error', 418);
      expect(error.statusCode).to.equal(418);
    });

    it('should default statusCode to 500', () => {
      const error = new HttpError('CUSTOM', 'HTTP error');
      expect(error.statusCode).to.equal(500);
    });

    it('should preserve error message', () => {
      const error = new HttpError('CUSTOM', 'HTTP error', 500);
      expect(error.message).to.equal('HTTP error');
    });

    it('should be instanceof BaseError', () => {
      const error = new HttpError('CUSTOM', 'HTTP error', 500);
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should be instanceof Error', () => {
      const error = new HttpError('CUSTOM', 'HTTP error', 500);
      expect(error).to.be.an.instanceOf(Error);
    });

    it('should accept metadata', () => {
      const metadata = { url: '/api/test' };
      const error = new HttpError('CUSTOM', 'HTTP error', 500, metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new HttpError('CUSTOM', 'HTTP error', 500);
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new HttpError('CUSTOM', 'HTTP error', 500);
      const json = error.toJSON();
      expect(json).to.have.property('name', 'HttpError');
      expect(json).to.have.property('code', 'HTTP_CUSTOM');
      expect(json).to.have.property('statusCode', 500);
      expect(json).to.have.property('message', 'HTTP error');
    });
  });

  describe('BadRequestError', () => {
    it('should have correct name', () => {
      const error = new BadRequestError('Bad request');
      expect(error.name).to.equal('BadRequestError');
    });

    it('should have correct code', () => {
      const error = new BadRequestError('Bad request');
      expect(error.code).to.equal('HTTP_BAD_REQUEST');
    });

    it('should have correct statusCode of 400', () => {
      const error = new BadRequestError('Bad request');
      expect(error.statusCode).to.equal(400);
    });

    it('should preserve error message', () => {
      const error = new BadRequestError('Bad request');
      expect(error.message).to.equal('Bad request');
    });

    it('should be instanceof HttpError', () => {
      const error = new BadRequestError('Bad request');
      expect(error).to.be.an.instanceOf(HttpError);
    });

    it('should be instanceof BaseError', () => {
      const error = new BadRequestError('Bad request');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { field: 'email', reason: 'invalid format' };
      const error = new BadRequestError('Bad request', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new BadRequestError('Bad request');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new BadRequestError('Bad request');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'BadRequestError');
      expect(json).to.have.property('code', 'HTTP_BAD_REQUEST');
      expect(json).to.have.property('statusCode', 400);
    });
  });

  describe('UnauthorizedError', () => {
    it('should have correct name', () => {
      const error = new UnauthorizedError('Unauthorized');
      expect(error.name).to.equal('UnauthorizedError');
    });

    it('should have correct code', () => {
      const error = new UnauthorizedError('Unauthorized');
      expect(error.code).to.equal('HTTP_UNAUTHORIZED');
    });

    it('should have correct statusCode of 401', () => {
      const error = new UnauthorizedError('Unauthorized');
      expect(error.statusCode).to.equal(401);
    });

    it('should preserve error message', () => {
      const error = new UnauthorizedError('Unauthorized');
      expect(error.message).to.equal('Unauthorized');
    });

    it('should be instanceof HttpError', () => {
      const error = new UnauthorizedError('Unauthorized');
      expect(error).to.be.an.instanceOf(HttpError);
    });

    it('should be instanceof BaseError', () => {
      const error = new UnauthorizedError('Unauthorized');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { reason: 'token expired' };
      const error = new UnauthorizedError('Unauthorized', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new UnauthorizedError('Unauthorized');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new UnauthorizedError('Unauthorized');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'UnauthorizedError');
      expect(json).to.have.property('code', 'HTTP_UNAUTHORIZED');
      expect(json).to.have.property('statusCode', 401);
    });
  });

  describe('GoneError', () => {
    it('should have correct name', () => {
      const error = new GoneError('Resource is gone');
      expect(error.name).to.equal('GoneError');
    });

    it('should have correct code', () => {
      const error = new GoneError('Resource is gone');
      expect(error.code).to.equal('HTTP_GONE');
    });

    it('should have correct statusCode of 410', () => {
      const error = new GoneError('Resource is gone');
      expect(error.statusCode).to.equal(410);
    });

    it('should preserve error message', () => {
      const error = new GoneError('Resource is gone');
      expect(error.message).to.equal('Resource is gone');
    });

    it('should be instanceof HttpError', () => {
      const error = new GoneError('Resource is gone');
      expect(error).to.be.an.instanceOf(HttpError);
    });

    it('should be instanceof BaseError', () => {
      const error = new GoneError('Resource is gone');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { resourceId: 'res-123' };
      const error = new GoneError('Resource is gone', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new GoneError('Resource is gone');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new GoneError('Resource is gone');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'GoneError');
      expect(json).to.have.property('code', 'HTTP_GONE');
      expect(json).to.have.property('statusCode', 410);
    });
  });

  describe('LargePayloadError', () => {
    it('should have correct name', () => {
      const error = new LargePayloadError('Payload too large');
      expect(error.name).to.equal('LargePayloadError');
    });

    it('should have correct code', () => {
      const error = new LargePayloadError('Payload too large');
      expect(error.code).to.equal('HTTP_PAYLOAD_TOO_LARGE');
    });

    it('should have correct statusCode of 413', () => {
      const error = new LargePayloadError('Payload too large');
      expect(error.statusCode).to.equal(413);
    });

    it('should preserve error message', () => {
      const error = new LargePayloadError('Payload too large');
      expect(error.message).to.equal('Payload too large');
    });

    it('should be instanceof HttpError', () => {
      const error = new LargePayloadError('Payload too large');
      expect(error).to.be.an.instanceOf(HttpError);
    });

    it('should be instanceof BaseError', () => {
      const error = new LargePayloadError('Payload too large');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { maxSize: '10MB', actualSize: '25MB' };
      const error = new LargePayloadError('Payload too large', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new LargePayloadError('Payload too large');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new LargePayloadError('Payload too large');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'LargePayloadError');
      expect(json).to.have.property('code', 'HTTP_PAYLOAD_TOO_LARGE');
      expect(json).to.have.property('statusCode', 413);
    });
  });

  describe('ForbiddenError', () => {
    it('should have correct name', () => {
      const error = new ForbiddenError('Forbidden');
      expect(error.name).to.equal('ForbiddenError');
    });

    it('should have correct code', () => {
      const error = new ForbiddenError('Forbidden');
      expect(error.code).to.equal('HTTP_FORBIDDEN');
    });

    it('should have correct statusCode of 403', () => {
      const error = new ForbiddenError('Forbidden');
      expect(error.statusCode).to.equal(403);
    });

    it('should preserve error message', () => {
      const error = new ForbiddenError('Forbidden');
      expect(error.message).to.equal('Forbidden');
    });

    it('should be instanceof HttpError', () => {
      const error = new ForbiddenError('Forbidden');
      expect(error).to.be.an.instanceOf(HttpError);
    });

    it('should be instanceof BaseError', () => {
      const error = new ForbiddenError('Forbidden');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { requiredRole: 'admin' };
      const error = new ForbiddenError('Forbidden', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new ForbiddenError('Forbidden');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new ForbiddenError('Forbidden');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'ForbiddenError');
      expect(json).to.have.property('code', 'HTTP_FORBIDDEN');
      expect(json).to.have.property('statusCode', 403);
    });
  });

  describe('NotFoundError', () => {
    it('should have correct name', () => {
      const error = new NotFoundError('Not found');
      expect(error.name).to.equal('NotFoundError');
    });

    it('should have correct code', () => {
      const error = new NotFoundError('Not found');
      expect(error.code).to.equal('HTTP_NOT_FOUND');
    });

    it('should have correct statusCode of 404', () => {
      const error = new NotFoundError('Not found');
      expect(error.statusCode).to.equal(404);
    });

    it('should preserve error message', () => {
      const error = new NotFoundError('Not found');
      expect(error.message).to.equal('Not found');
    });

    it('should be instanceof HttpError', () => {
      const error = new NotFoundError('Not found');
      expect(error).to.be.an.instanceOf(HttpError);
    });

    it('should be instanceof BaseError', () => {
      const error = new NotFoundError('Not found');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { resource: 'user', id: '123' };
      const error = new NotFoundError('Not found', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new NotFoundError('Not found');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new NotFoundError('Not found');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'NotFoundError');
      expect(json).to.have.property('code', 'HTTP_NOT_FOUND');
      expect(json).to.have.property('statusCode', 404);
    });
  });

  describe('ConflictError', () => {
    it('should have correct name', () => {
      const error = new ConflictError('Conflict');
      expect(error.name).to.equal('ConflictError');
    });

    it('should have correct code', () => {
      const error = new ConflictError('Conflict');
      expect(error.code).to.equal('HTTP_CONFLICT');
    });

    it('should have correct statusCode of 409', () => {
      const error = new ConflictError('Conflict');
      expect(error.statusCode).to.equal(409);
    });

    it('should preserve error message', () => {
      const error = new ConflictError('Conflict');
      expect(error.message).to.equal('Conflict');
    });

    it('should be instanceof HttpError', () => {
      const error = new ConflictError('Conflict');
      expect(error).to.be.an.instanceOf(HttpError);
    });

    it('should be instanceof BaseError', () => {
      const error = new ConflictError('Conflict');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { existingId: 'abc-123' };
      const error = new ConflictError('Conflict', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new ConflictError('Conflict');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new ConflictError('Conflict');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'ConflictError');
      expect(json).to.have.property('code', 'HTTP_CONFLICT');
      expect(json).to.have.property('statusCode', 409);
    });
  });

  describe('TooManyRequestsError', () => {
    it('should have correct name', () => {
      const error = new TooManyRequestsError('Rate limit exceeded');
      expect(error.name).to.equal('TooManyRequestsError');
    });

    it('should have correct code', () => {
      const error = new TooManyRequestsError('Rate limit exceeded');
      expect(error.code).to.equal('HTTP_TOO_MANY_REQUESTS');
    });

    it('should have correct statusCode of 429', () => {
      const error = new TooManyRequestsError('Rate limit exceeded');
      expect(error.statusCode).to.equal(429);
    });

    it('should preserve error message', () => {
      const error = new TooManyRequestsError('Rate limit exceeded');
      expect(error.message).to.equal('Rate limit exceeded');
    });

    it('should be instanceof HttpError', () => {
      const error = new TooManyRequestsError('Rate limit exceeded');
      expect(error).to.be.an.instanceOf(HttpError);
    });

    it('should be instanceof BaseError', () => {
      const error = new TooManyRequestsError('Rate limit exceeded');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { retryAfter: 60 };
      const error = new TooManyRequestsError('Rate limit exceeded', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new TooManyRequestsError('Rate limit exceeded');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new TooManyRequestsError('Rate limit exceeded');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'TooManyRequestsError');
      expect(json).to.have.property('code', 'HTTP_TOO_MANY_REQUESTS');
      expect(json).to.have.property('statusCode', 429);
    });
  });

  describe('InternalServerError', () => {
    it('should have correct name', () => {
      const error = new InternalServerError('Internal server error');
      expect(error.name).to.equal('InternalServerError');
    });

    it('should have correct code', () => {
      const error = new InternalServerError('Internal server error');
      expect(error.code).to.equal('HTTP_INTERNAL_SERVER_ERROR');
    });

    it('should have correct statusCode of 500', () => {
      const error = new InternalServerError('Internal server error');
      expect(error.statusCode).to.equal(500);
    });

    it('should preserve error message', () => {
      const error = new InternalServerError('Internal server error');
      expect(error.message).to.equal('Internal server error');
    });

    it('should be instanceof HttpError', () => {
      const error = new InternalServerError('Internal server error');
      expect(error).to.be.an.instanceOf(HttpError);
    });

    it('should be instanceof BaseError', () => {
      const error = new InternalServerError('Internal server error');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { service: 'auth' };
      const error = new InternalServerError('Internal server error', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new InternalServerError('Internal server error');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new InternalServerError('Internal server error');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'InternalServerError');
      expect(json).to.have.property('code', 'HTTP_INTERNAL_SERVER_ERROR');
      expect(json).to.have.property('statusCode', 500);
    });
  });

  describe('ServiceUnavailableError', () => {
    it('should have correct name', () => {
      const error = new ServiceUnavailableError('Service unavailable');
      expect(error.name).to.equal('ServiceUnavailableError');
    });

    it('should have correct code', () => {
      const error = new ServiceUnavailableError('Service unavailable');
      expect(error.code).to.equal('HTTP_SERVICE_UNAVAILABLE');
    });

    it('should have correct statusCode of 503', () => {
      const error = new ServiceUnavailableError('Service unavailable');
      expect(error.statusCode).to.equal(503);
    });

    it('should preserve error message', () => {
      const error = new ServiceUnavailableError('Service unavailable');
      expect(error.message).to.equal('Service unavailable');
    });

    it('should be instanceof HttpError', () => {
      const error = new ServiceUnavailableError('Service unavailable');
      expect(error).to.be.an.instanceOf(HttpError);
    });

    it('should be instanceof BaseError', () => {
      const error = new ServiceUnavailableError('Service unavailable');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { service: 'database', retryAfter: 30 };
      const error = new ServiceUnavailableError('Service unavailable', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new ServiceUnavailableError('Service unavailable');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new ServiceUnavailableError('Service unavailable');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'ServiceUnavailableError');
      expect(json).to.have.property('code', 'HTTP_SERVICE_UNAVAILABLE');
      expect(json).to.have.property('statusCode', 503);
    });
  });

  describe('BadGatewayError', () => {
    it('should have correct name', () => {
      const error = new BadGatewayError('Bad gateway');
      expect(error.name).to.equal('BadGatewayError');
    });

    it('should have correct code', () => {
      const error = new BadGatewayError('Bad gateway');
      expect(error.code).to.equal('HTTP_BAD_GATEWAY');
    });

    it('should have correct statusCode of 502', () => {
      const error = new BadGatewayError('Bad gateway');
      expect(error.statusCode).to.equal(502);
    });

    it('should preserve error message', () => {
      const error = new BadGatewayError('Bad gateway');
      expect(error.message).to.equal('Bad gateway');
    });

    it('should be instanceof HttpError', () => {
      const error = new BadGatewayError('Bad gateway');
      expect(error).to.be.an.instanceOf(HttpError);
    });

    it('should be instanceof BaseError', () => {
      const error = new BadGatewayError('Bad gateway');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { upstreamService: 'auth-service' };
      const error = new BadGatewayError('Bad gateway', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new BadGatewayError('Bad gateway');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new BadGatewayError('Bad gateway');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'BadGatewayError');
      expect(json).to.have.property('code', 'HTTP_BAD_GATEWAY');
      expect(json).to.have.property('statusCode', 502);
    });
  });

  describe('GatewayTimeoutError', () => {
    it('should have correct name', () => {
      const error = new GatewayTimeoutError('Gateway timeout');
      expect(error.name).to.equal('GatewayTimeoutError');
    });

    it('should have correct code', () => {
      const error = new GatewayTimeoutError('Gateway timeout');
      expect(error.code).to.equal('HTTP_GATEWAY_TIMEOUT');
    });

    it('should have correct statusCode of 504', () => {
      const error = new GatewayTimeoutError('Gateway timeout');
      expect(error.statusCode).to.equal(504);
    });

    it('should preserve error message', () => {
      const error = new GatewayTimeoutError('Gateway timeout');
      expect(error.message).to.equal('Gateway timeout');
    });

    it('should be instanceof HttpError', () => {
      const error = new GatewayTimeoutError('Gateway timeout');
      expect(error).to.be.an.instanceOf(HttpError);
    });

    it('should be instanceof BaseError', () => {
      const error = new GatewayTimeoutError('Gateway timeout');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { upstreamTimeout: 30000 };
      const error = new GatewayTimeoutError('Gateway timeout', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new GatewayTimeoutError('Gateway timeout');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new GatewayTimeoutError('Gateway timeout');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'GatewayTimeoutError');
      expect(json).to.have.property('code', 'HTTP_GATEWAY_TIMEOUT');
      expect(json).to.have.property('statusCode', 504);
    });
  });

  describe('UnprocessableEntityError', () => {
    it('should have correct name', () => {
      const error = new UnprocessableEntityError('Unprocessable entity');
      expect(error.name).to.equal('UnprocessableEntityError');
    });

    it('should have correct code', () => {
      const error = new UnprocessableEntityError('Unprocessable entity');
      expect(error.code).to.equal('HTTP_UNPROCESSABLE_ENTITY');
    });

    it('should have correct statusCode of 422', () => {
      const error = new UnprocessableEntityError('Unprocessable entity');
      expect(error.statusCode).to.equal(422);
    });

    it('should preserve error message', () => {
      const error = new UnprocessableEntityError('Unprocessable entity');
      expect(error.message).to.equal('Unprocessable entity');
    });

    it('should be instanceof HttpError', () => {
      const error = new UnprocessableEntityError('Unprocessable entity');
      expect(error).to.be.an.instanceOf(HttpError);
    });

    it('should be instanceof BaseError', () => {
      const error = new UnprocessableEntityError('Unprocessable entity');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { validationErrors: ['name is required'] };
      const error = new UnprocessableEntityError('Unprocessable entity', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new UnprocessableEntityError('Unprocessable entity');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new UnprocessableEntityError('Unprocessable entity');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'UnprocessableEntityError');
      expect(json).to.have.property('code', 'HTTP_UNPROCESSABLE_ENTITY');
      expect(json).to.have.property('statusCode', 422);
    });
  });

  describe('NotImplementedError', () => {
    it('should have correct name', () => {
      const error = new NotImplementedError('Not implemented');
      expect(error.name).to.equal('NotImplementedError');
    });

    it('should have correct code', () => {
      const error = new NotImplementedError('Not implemented');
      expect(error.code).to.equal('HTTP_NOT_IMPLEMENTED');
    });

    it('should have correct statusCode of 501', () => {
      const error = new NotImplementedError('Not implemented');
      expect(error.statusCode).to.equal(501);
    });

    it('should preserve error message', () => {
      const error = new NotImplementedError('Not implemented');
      expect(error.message).to.equal('Not implemented');
    });

    it('should be instanceof HttpError', () => {
      const error = new NotImplementedError('Not implemented');
      expect(error).to.be.an.instanceOf(HttpError);
    });

    it('should be instanceof BaseError', () => {
      const error = new NotImplementedError('Not implemented');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { feature: 'bulk-export' };
      const error = new NotImplementedError('Not implemented', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new NotImplementedError('Not implemented');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new NotImplementedError('Not implemented');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'NotImplementedError');
      expect(json).to.have.property('code', 'HTTP_NOT_IMPLEMENTED');
      expect(json).to.have.property('statusCode', 501);
    });
  });
});
