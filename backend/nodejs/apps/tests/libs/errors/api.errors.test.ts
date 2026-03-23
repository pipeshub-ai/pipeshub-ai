import { expect } from 'chai';
import { BaseError } from '../../../src/libs/errors/base.error';
import {
  APIError,
  APIRequestError,
  APIResponseError,
  APITimeoutError,
} from '../../../src/libs/errors/api.errors';

describe('API Errors', () => {
  describe('APIError', () => {
    it('should have correct name', () => {
      const error = new APIError('CUSTOM', 'API failed', 500);
      expect(error.name).to.equal('APIError');
    });

    it('should have correct code with API_ prefix', () => {
      const error = new APIError('CUSTOM', 'API failed', 500);
      expect(error.code).to.equal('API_CUSTOM');
    });

    it('should have correct statusCode', () => {
      const error = new APIError('CUSTOM', 'API failed', 502);
      expect(error.statusCode).to.equal(502);
    });

    it('should default statusCode to 500', () => {
      const error = new APIError('CUSTOM', 'API failed');
      expect(error.statusCode).to.equal(500);
    });

    it('should preserve error message', () => {
      const error = new APIError('CUSTOM', 'API failed', 500);
      expect(error.message).to.equal('API failed');
    });

    it('should be instanceof BaseError', () => {
      const error = new APIError('CUSTOM', 'API failed', 500);
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should be instanceof Error', () => {
      const error = new APIError('CUSTOM', 'API failed', 500);
      expect(error).to.be.an.instanceOf(Error);
    });

    it('should accept metadata', () => {
      const metadata = { endpoint: '/api/users', method: 'GET' };
      const error = new APIError('CUSTOM', 'API failed', 500, metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new APIError('CUSTOM', 'API failed', 500);
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new APIError('CUSTOM', 'API failed', 500);
      const json = error.toJSON();
      expect(json).to.have.property('name', 'APIError');
      expect(json).to.have.property('code', 'API_CUSTOM');
      expect(json).to.have.property('statusCode', 500);
      expect(json).to.have.property('message', 'API failed');
    });
  });

  describe('APIRequestError', () => {
    it('should have correct name', () => {
      const error = new APIRequestError('Bad request data');
      expect(error.name).to.equal('APIRequestError');
    });

    it('should have correct code', () => {
      const error = new APIRequestError('Bad request data');
      expect(error.code).to.equal('API_REQUEST_ERROR');
    });

    it('should have correct statusCode of 400', () => {
      const error = new APIRequestError('Bad request data');
      expect(error.statusCode).to.equal(400);
    });

    it('should preserve error message', () => {
      const error = new APIRequestError('Bad request data');
      expect(error.message).to.equal('Bad request data');
    });

    it('should be instanceof APIError', () => {
      const error = new APIRequestError('Bad request data');
      expect(error).to.be.an.instanceOf(APIError);
    });

    it('should be instanceof BaseError', () => {
      const error = new APIRequestError('Bad request data');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { field: 'email' };
      const error = new APIRequestError('Bad request data', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new APIRequestError('Bad request data');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new APIRequestError('Bad request data');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'APIRequestError');
      expect(json).to.have.property('code', 'API_REQUEST_ERROR');
      expect(json).to.have.property('statusCode', 400);
      expect(json).to.have.property('message', 'Bad request data');
    });
  });

  describe('APIResponseError', () => {
    it('should have correct name', () => {
      const error = new APIResponseError('Response parsing failed');
      expect(error.name).to.equal('APIResponseError');
    });

    it('should have correct code', () => {
      const error = new APIResponseError('Response parsing failed');
      expect(error.code).to.equal('API_RESPONSE_ERROR');
    });

    it('should have correct statusCode of 500', () => {
      const error = new APIResponseError('Response parsing failed');
      expect(error.statusCode).to.equal(500);
    });

    it('should preserve error message', () => {
      const error = new APIResponseError('Response parsing failed');
      expect(error.message).to.equal('Response parsing failed');
    });

    it('should be instanceof APIError', () => {
      const error = new APIResponseError('Response parsing failed');
      expect(error).to.be.an.instanceOf(APIError);
    });

    it('should be instanceof BaseError', () => {
      const error = new APIResponseError('Response parsing failed');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { responseBody: 'invalid json' };
      const error = new APIResponseError('Response parsing failed', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new APIResponseError('Response parsing failed');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new APIResponseError('Response parsing failed');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'APIResponseError');
      expect(json).to.have.property('code', 'API_RESPONSE_ERROR');
      expect(json).to.have.property('statusCode', 500);
      expect(json).to.have.property('message', 'Response parsing failed');
    });
  });

  describe('APITimeoutError', () => {
    it('should have correct name', () => {
      const error = new APITimeoutError('Request timed out');
      expect(error.name).to.equal('APITimeoutError');
    });

    it('should have correct code', () => {
      const error = new APITimeoutError('Request timed out');
      expect(error.code).to.equal('API_TIMEOUT');
    });

    it('should have correct statusCode of 504', () => {
      const error = new APITimeoutError('Request timed out');
      expect(error.statusCode).to.equal(504);
    });

    it('should preserve error message', () => {
      const error = new APITimeoutError('Request timed out');
      expect(error.message).to.equal('Request timed out');
    });

    it('should be instanceof APIError', () => {
      const error = new APITimeoutError('Request timed out');
      expect(error).to.be.an.instanceOf(APIError);
    });

    it('should be instanceof BaseError', () => {
      const error = new APITimeoutError('Request timed out');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { timeoutMs: 30000, endpoint: '/api/data' };
      const error = new APITimeoutError('Request timed out', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new APITimeoutError('Request timed out');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new APITimeoutError('Request timed out');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'APITimeoutError');
      expect(json).to.have.property('code', 'API_TIMEOUT');
      expect(json).to.have.property('statusCode', 504);
      expect(json).to.have.property('message', 'Request timed out');
    });
  });
});
