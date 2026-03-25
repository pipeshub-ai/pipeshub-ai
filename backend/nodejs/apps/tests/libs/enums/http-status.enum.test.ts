import { expect } from 'chai';
import { HTTP_STATUS } from '../../../src/libs/enums/http-status.enum';

describe('HTTP_STATUS', () => {
  // 2xx Success
  describe('2xx Success codes', () => {
    it('should have OK as 200', () => {
      expect(HTTP_STATUS.OK).to.equal(200);
    });

    it('should have CREATED as 201', () => {
      expect(HTTP_STATUS.CREATED).to.equal(201);
    });

    it('should have ACCEPTED as 202', () => {
      expect(HTTP_STATUS.ACCEPTED).to.equal(202);
    });

    it('should have NO_CONTENT as 204', () => {
      expect(HTTP_STATUS.NO_CONTENT).to.equal(204);
    });
  });

  // 3xx Redirection
  describe('3xx Redirection codes', () => {
    it('should have PERMANENT_REDIRECT as 301', () => {
      expect(HTTP_STATUS.PERMANENT_REDIRECT).to.equal(301);
    });
  });

  // 4xx Client Error
  describe('4xx Client Error codes', () => {
    it('should have BAD_REQUEST as 400', () => {
      expect(HTTP_STATUS.BAD_REQUEST).to.equal(400);
    });

    it('should have UNAUTHORIZED as 401', () => {
      expect(HTTP_STATUS.UNAUTHORIZED).to.equal(401);
    });

    it('should have FORBIDDEN as 403', () => {
      expect(HTTP_STATUS.FORBIDDEN).to.equal(403);
    });

    it('should have NOT_FOUND as 404', () => {
      expect(HTTP_STATUS.NOT_FOUND).to.equal(404);
    });

    it('should have METHOD_NOT_ALLOWED as 405', () => {
      expect(HTTP_STATUS.METHOD_NOT_ALLOWED).to.equal(405);
    });

    it('should have CONFLICT as 409', () => {
      expect(HTTP_STATUS.CONFLICT).to.equal(409);
    });

    it('should have GONE as 410', () => {
      expect(HTTP_STATUS.GONE).to.equal(410);
    });

    it('should have PAYLOAD_TOO_LARGE as 413', () => {
      expect(HTTP_STATUS.PAYLOAD_TOO_LARGE).to.equal(413);
    });

    it('should have UNSUPPORTED_MEDIA_TYPE as 415', () => {
      expect(HTTP_STATUS.UNSUPPORTED_MEDIA_TYPE).to.equal(415);
    });

    it('should have UNPROCESSABLE_ENTITY as 422', () => {
      expect(HTTP_STATUS.UNPROCESSABLE_ENTITY).to.equal(422);
    });

    it('should have TOO_MANY_REQUESTS as 429', () => {
      expect(HTTP_STATUS.TOO_MANY_REQUESTS).to.equal(429);
    });
  });

  // 5xx Server Error
  describe('5xx Server Error codes', () => {
    it('should have INTERNAL_SERVER as 500', () => {
      expect(HTTP_STATUS.INTERNAL_SERVER).to.equal(500);
    });

    it('should have NOT_IMPLEMENTED as 501', () => {
      expect(HTTP_STATUS.NOT_IMPLEMENTED).to.equal(501);
    });

    it('should have BAD_GATEWAY as 502', () => {
      expect(HTTP_STATUS.BAD_GATEWAY).to.equal(502);
    });

    it('should have SERVICE_UNAVAILABLE as 503', () => {
      expect(HTTP_STATUS.SERVICE_UNAVAILABLE).to.equal(503);
    });

    it('should have GATEWAY_TIMEOUT as 504', () => {
      expect(HTTP_STATUS.GATEWAY_TIMEOUT).to.equal(504);
    });
  });

  // Structural tests
  describe('structural checks', () => {
    it('should have exactly 21 status codes', () => {
      expect(Object.keys(HTTP_STATUS)).to.have.lengthOf(21);
    });

    it('should contain only the expected keys', () => {
      const expectedKeys = [
        'OK',
        'CREATED',
        'ACCEPTED',
        'NO_CONTENT',
        'PERMANENT_REDIRECT',
        'BAD_REQUEST',
        'UNAUTHORIZED',
        'FORBIDDEN',
        'NOT_FOUND',
        'METHOD_NOT_ALLOWED',
        'CONFLICT',
        'GONE',
        'PAYLOAD_TOO_LARGE',
        'UNSUPPORTED_MEDIA_TYPE',
        'TOO_MANY_REQUESTS',
        'UNPROCESSABLE_ENTITY',
        'INTERNAL_SERVER',
        'NOT_IMPLEMENTED',
        'BAD_GATEWAY',
        'SERVICE_UNAVAILABLE',
        'GATEWAY_TIMEOUT',
      ];
      expect(Object.keys(HTTP_STATUS)).to.have.members(expectedKeys);
    });

    it('should have all values as numbers', () => {
      Object.values(HTTP_STATUS).forEach((value) => {
        expect(value).to.be.a('number');
      });
    });

    it('should have no duplicate values', () => {
      const values = Object.values(HTTP_STATUS);
      const uniqueValues = new Set(values);
      expect(uniqueValues.size).to.equal(values.length);
    });
  });
});
