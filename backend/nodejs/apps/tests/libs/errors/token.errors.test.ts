import { expect } from 'chai';
import { BaseError } from '../../../src/libs/errors/base.error';
import { TokenError } from '../../../src/libs/errors/token.errors';

describe('Token Errors', () => {
  describe('TokenError', () => {
    it('should have correct name', () => {
      const error = new TokenError('Token is invalid');
      expect(error.name).to.equal('TokenError');
    });

    it('should have correct code', () => {
      const error = new TokenError('Token is invalid');
      expect(error.code).to.equal('TOKEN_ERROR');
    });

    it('should have correct statusCode of 401', () => {
      const error = new TokenError('Token is invalid');
      expect(error.statusCode).to.equal(401);
    });

    it('should preserve error message', () => {
      const error = new TokenError('Token is invalid');
      expect(error.message).to.equal('Token is invalid');
    });

    it('should be instanceof BaseError', () => {
      const error = new TokenError('Token is invalid');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should be instanceof Error', () => {
      const error = new TokenError('Token is invalid');
      expect(error).to.be.an.instanceOf(Error);
    });

    it('should accept metadata', () => {
      const metadata = { tokenType: 'JWT', reason: 'expired' };
      const error = new TokenError('Token is invalid', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should leave metadata undefined when not provided', () => {
      const error = new TokenError('Token is invalid');
      expect(error.metadata).to.be.undefined;
    });

    it('should have a stack trace', () => {
      const error = new TokenError('Token is invalid');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new TokenError('Token is invalid');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'TokenError');
      expect(json).to.have.property('code', 'TOKEN_ERROR');
      expect(json).to.have.property('statusCode', 401);
      expect(json).to.have.property('message', 'Token is invalid');
    });

    it('should include metadata in JSON output when provided', () => {
      const metadata = { tokenType: 'JWT' };
      const error = new TokenError('Token is invalid', metadata);
      const json = error.toJSON();
      expect(json.metadata).to.deep.equal(metadata);
    });
  });
});
