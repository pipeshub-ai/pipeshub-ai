import { expect } from 'chai';
import { BaseError } from '../../../src/libs/errors/base.error';
import {
  OAuthError,
  InvalidGrantError,
  InvalidTokenError,
  ExpiredTokenError,
  InvalidScopeError,
  AuthorizationError,
  InvalidClientError,
  InvalidRedirectUriError,
  UnsupportedGrantTypeError,
  AccessDeniedError,
  UnauthorizedClientError,
  ServerError,
} from '../../../src/libs/errors/oauth.errors';

describe('OAuth Errors', () => {
  describe('OAuthError', () => {
    it('should have correct name', () => {
      const error = new OAuthError('CUSTOM', 'OAuth failed', 500);
      expect(error.name).to.equal('OAuthError');
    });

    it('should have correct code with OAUTH_ prefix', () => {
      const error = new OAuthError('CUSTOM', 'OAuth failed', 500);
      expect(error.code).to.equal('OAUTH_CUSTOM');
    });

    it('should have correct statusCode', () => {
      const error = new OAuthError('CUSTOM', 'OAuth failed', 401);
      expect(error.statusCode).to.equal(401);
    });

    it('should default statusCode to 500', () => {
      const error = new OAuthError('CUSTOM', 'OAuth failed');
      expect(error.statusCode).to.equal(500);
    });

    it('should preserve error message', () => {
      const error = new OAuthError('CUSTOM', 'OAuth failed', 500);
      expect(error.message).to.equal('OAuth failed');
    });

    it('should be instanceof BaseError', () => {
      const error = new OAuthError('CUSTOM', 'OAuth failed', 500);
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should be instanceof Error', () => {
      const error = new OAuthError('CUSTOM', 'OAuth failed', 500);
      expect(error).to.be.an.instanceOf(Error);
    });

    it('should accept metadata', () => {
      const metadata = { provider: 'google' };
      const error = new OAuthError('CUSTOM', 'OAuth failed', 500, metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new OAuthError('CUSTOM', 'OAuth failed', 500);
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new OAuthError('CUSTOM', 'OAuth failed', 500);
      const json = error.toJSON();
      expect(json).to.have.property('name', 'OAuthError');
      expect(json).to.have.property('code', 'OAUTH_CUSTOM');
      expect(json).to.have.property('statusCode', 500);
      expect(json).to.have.property('message', 'OAuth failed');
    });
  });

  describe('InvalidGrantError', () => {
    it('should have correct name', () => {
      const error = new InvalidGrantError('Invalid grant');
      expect(error.name).to.equal('InvalidGrantError');
    });

    it('should have correct code', () => {
      const error = new InvalidGrantError('Invalid grant');
      expect(error.code).to.equal('OAUTH_INVALID_GRANT');
    });

    it('should have correct statusCode of 400', () => {
      const error = new InvalidGrantError('Invalid grant');
      expect(error.statusCode).to.equal(400);
    });

    it('should preserve error message', () => {
      const error = new InvalidGrantError('Invalid grant');
      expect(error.message).to.equal('Invalid grant');
    });

    it('should be instanceof OAuthError', () => {
      const error = new InvalidGrantError('Invalid grant');
      expect(error).to.be.an.instanceOf(OAuthError);
    });

    it('should be instanceof BaseError', () => {
      const error = new InvalidGrantError('Invalid grant');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { grantType: 'authorization_code' };
      const error = new InvalidGrantError('Invalid grant', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new InvalidGrantError('Invalid grant');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new InvalidGrantError('Invalid grant');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'InvalidGrantError');
      expect(json).to.have.property('code', 'OAUTH_INVALID_GRANT');
      expect(json).to.have.property('statusCode', 400);
    });
  });

  describe('InvalidTokenError', () => {
    it('should have correct name', () => {
      const error = new InvalidTokenError('Invalid token');
      expect(error.name).to.equal('InvalidTokenError');
    });

    it('should have correct code', () => {
      const error = new InvalidTokenError('Invalid token');
      expect(error.code).to.equal('OAUTH_INVALID_TOKEN');
    });

    it('should have correct statusCode of 401', () => {
      const error = new InvalidTokenError('Invalid token');
      expect(error.statusCode).to.equal(401);
    });

    it('should preserve error message', () => {
      const error = new InvalidTokenError('Invalid token');
      expect(error.message).to.equal('Invalid token');
    });

    it('should be instanceof OAuthError', () => {
      const error = new InvalidTokenError('Invalid token');
      expect(error).to.be.an.instanceOf(OAuthError);
    });

    it('should be instanceof BaseError', () => {
      const error = new InvalidTokenError('Invalid token');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { tokenType: 'access_token' };
      const error = new InvalidTokenError('Invalid token', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new InvalidTokenError('Invalid token');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new InvalidTokenError('Invalid token');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'InvalidTokenError');
      expect(json).to.have.property('code', 'OAUTH_INVALID_TOKEN');
      expect(json).to.have.property('statusCode', 401);
    });
  });

  describe('ExpiredTokenError', () => {
    it('should have correct name', () => {
      const error = new ExpiredTokenError('Token expired');
      expect(error.name).to.equal('ExpiredTokenError');
    });

    it('should have correct code', () => {
      const error = new ExpiredTokenError('Token expired');
      expect(error.code).to.equal('OAUTH_EXPIRED_TOKEN');
    });

    it('should have correct statusCode of 401', () => {
      const error = new ExpiredTokenError('Token expired');
      expect(error.statusCode).to.equal(401);
    });

    it('should preserve error message', () => {
      const error = new ExpiredTokenError('Token expired');
      expect(error.message).to.equal('Token expired');
    });

    it('should be instanceof OAuthError', () => {
      const error = new ExpiredTokenError('Token expired');
      expect(error).to.be.an.instanceOf(OAuthError);
    });

    it('should be instanceof BaseError', () => {
      const error = new ExpiredTokenError('Token expired');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { expiredAt: '2024-01-01T00:00:00Z' };
      const error = new ExpiredTokenError('Token expired', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new ExpiredTokenError('Token expired');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new ExpiredTokenError('Token expired');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'ExpiredTokenError');
      expect(json).to.have.property('code', 'OAUTH_EXPIRED_TOKEN');
      expect(json).to.have.property('statusCode', 401);
    });
  });

  describe('InvalidScopeError', () => {
    it('should have correct name', () => {
      const error = new InvalidScopeError('Invalid scope');
      expect(error.name).to.equal('InvalidScopeError');
    });

    it('should have correct code', () => {
      const error = new InvalidScopeError('Invalid scope');
      expect(error.code).to.equal('OAUTH_INVALID_SCOPE');
    });

    it('should have correct statusCode of 400', () => {
      const error = new InvalidScopeError('Invalid scope');
      expect(error.statusCode).to.equal(400);
    });

    it('should preserve error message', () => {
      const error = new InvalidScopeError('Invalid scope');
      expect(error.message).to.equal('Invalid scope');
    });

    it('should be instanceof OAuthError', () => {
      const error = new InvalidScopeError('Invalid scope');
      expect(error).to.be.an.instanceOf(OAuthError);
    });

    it('should be instanceof BaseError', () => {
      const error = new InvalidScopeError('Invalid scope');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { requestedScope: 'admin', allowedScopes: ['read', 'write'] };
      const error = new InvalidScopeError('Invalid scope', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new InvalidScopeError('Invalid scope');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new InvalidScopeError('Invalid scope');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'InvalidScopeError');
      expect(json).to.have.property('code', 'OAUTH_INVALID_SCOPE');
      expect(json).to.have.property('statusCode', 400);
    });
  });

  describe('AuthorizationError', () => {
    it('should have correct name', () => {
      const error = new AuthorizationError('Authorization failed');
      expect(error.name).to.equal('AuthorizationError');
    });

    it('should have correct code', () => {
      const error = new AuthorizationError('Authorization failed');
      expect(error.code).to.equal('OAUTH_AUTHORIZATION_ERROR');
    });

    it('should have correct statusCode of 403', () => {
      const error = new AuthorizationError('Authorization failed');
      expect(error.statusCode).to.equal(403);
    });

    it('should preserve error message', () => {
      const error = new AuthorizationError('Authorization failed');
      expect(error.message).to.equal('Authorization failed');
    });

    it('should be instanceof OAuthError', () => {
      const error = new AuthorizationError('Authorization failed');
      expect(error).to.be.an.instanceOf(OAuthError);
    });

    it('should be instanceof BaseError', () => {
      const error = new AuthorizationError('Authorization failed');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { userId: 'user-123' };
      const error = new AuthorizationError('Authorization failed', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new AuthorizationError('Authorization failed');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new AuthorizationError('Authorization failed');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'AuthorizationError');
      expect(json).to.have.property('code', 'OAUTH_AUTHORIZATION_ERROR');
      expect(json).to.have.property('statusCode', 403);
    });
  });

  describe('InvalidClientError', () => {
    it('should have correct name', () => {
      const error = new InvalidClientError('Invalid client');
      expect(error.name).to.equal('InvalidClientError');
    });

    it('should have correct code', () => {
      const error = new InvalidClientError('Invalid client');
      expect(error.code).to.equal('OAUTH_INVALID_CLIENT');
    });

    it('should have correct statusCode of 401', () => {
      const error = new InvalidClientError('Invalid client');
      expect(error.statusCode).to.equal(401);
    });

    it('should preserve error message', () => {
      const error = new InvalidClientError('Invalid client');
      expect(error.message).to.equal('Invalid client');
    });

    it('should be instanceof OAuthError', () => {
      const error = new InvalidClientError('Invalid client');
      expect(error).to.be.an.instanceOf(OAuthError);
    });

    it('should be instanceof BaseError', () => {
      const error = new InvalidClientError('Invalid client');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { clientId: 'client-xyz' };
      const error = new InvalidClientError('Invalid client', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new InvalidClientError('Invalid client');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new InvalidClientError('Invalid client');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'InvalidClientError');
      expect(json).to.have.property('code', 'OAUTH_INVALID_CLIENT');
      expect(json).to.have.property('statusCode', 401);
    });
  });

  describe('InvalidRedirectUriError', () => {
    it('should have correct name', () => {
      const error = new InvalidRedirectUriError('Invalid redirect URI');
      expect(error.name).to.equal('InvalidRedirectUriError');
    });

    it('should have correct code', () => {
      const error = new InvalidRedirectUriError('Invalid redirect URI');
      expect(error.code).to.equal('OAUTH_INVALID_REDIRECT_URI');
    });

    it('should have correct statusCode of 400', () => {
      const error = new InvalidRedirectUriError('Invalid redirect URI');
      expect(error.statusCode).to.equal(400);
    });

    it('should preserve error message', () => {
      const error = new InvalidRedirectUriError('Invalid redirect URI');
      expect(error.message).to.equal('Invalid redirect URI');
    });

    it('should be instanceof OAuthError', () => {
      const error = new InvalidRedirectUriError('Invalid redirect URI');
      expect(error).to.be.an.instanceOf(OAuthError);
    });

    it('should be instanceof BaseError', () => {
      const error = new InvalidRedirectUriError('Invalid redirect URI');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { uri: 'http://evil.com/callback' };
      const error = new InvalidRedirectUriError('Invalid redirect URI', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new InvalidRedirectUriError('Invalid redirect URI');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new InvalidRedirectUriError('Invalid redirect URI');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'InvalidRedirectUriError');
      expect(json).to.have.property('code', 'OAUTH_INVALID_REDIRECT_URI');
      expect(json).to.have.property('statusCode', 400);
    });
  });

  describe('UnsupportedGrantTypeError', () => {
    it('should have correct name', () => {
      const error = new UnsupportedGrantTypeError('Unsupported grant type');
      expect(error.name).to.equal('UnsupportedGrantTypeError');
    });

    it('should have correct code', () => {
      const error = new UnsupportedGrantTypeError('Unsupported grant type');
      expect(error.code).to.equal('OAUTH_UNSUPPORTED_GRANT_TYPE');
    });

    it('should have correct statusCode of 400', () => {
      const error = new UnsupportedGrantTypeError('Unsupported grant type');
      expect(error.statusCode).to.equal(400);
    });

    it('should preserve error message', () => {
      const error = new UnsupportedGrantTypeError('Unsupported grant type');
      expect(error.message).to.equal('Unsupported grant type');
    });

    it('should be instanceof OAuthError', () => {
      const error = new UnsupportedGrantTypeError('Unsupported grant type');
      expect(error).to.be.an.instanceOf(OAuthError);
    });

    it('should be instanceof BaseError', () => {
      const error = new UnsupportedGrantTypeError('Unsupported grant type');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { grantType: 'implicit' };
      const error = new UnsupportedGrantTypeError('Unsupported grant type', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new UnsupportedGrantTypeError('Unsupported grant type');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new UnsupportedGrantTypeError('Unsupported grant type');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'UnsupportedGrantTypeError');
      expect(json).to.have.property('code', 'OAUTH_UNSUPPORTED_GRANT_TYPE');
      expect(json).to.have.property('statusCode', 400);
    });
  });

  describe('AccessDeniedError', () => {
    it('should have correct name', () => {
      const error = new AccessDeniedError('Access denied');
      expect(error.name).to.equal('AccessDeniedError');
    });

    it('should have correct code', () => {
      const error = new AccessDeniedError('Access denied');
      expect(error.code).to.equal('OAUTH_ACCESS_DENIED');
    });

    it('should have correct statusCode of 403', () => {
      const error = new AccessDeniedError('Access denied');
      expect(error.statusCode).to.equal(403);
    });

    it('should preserve error message', () => {
      const error = new AccessDeniedError('Access denied');
      expect(error.message).to.equal('Access denied');
    });

    it('should be instanceof OAuthError', () => {
      const error = new AccessDeniedError('Access denied');
      expect(error).to.be.an.instanceOf(OAuthError);
    });

    it('should be instanceof BaseError', () => {
      const error = new AccessDeniedError('Access denied');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { resource: '/admin/settings' };
      const error = new AccessDeniedError('Access denied', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new AccessDeniedError('Access denied');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new AccessDeniedError('Access denied');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'AccessDeniedError');
      expect(json).to.have.property('code', 'OAUTH_ACCESS_DENIED');
      expect(json).to.have.property('statusCode', 403);
    });
  });

  describe('UnauthorizedClientError', () => {
    it('should have correct name', () => {
      const error = new UnauthorizedClientError('Unauthorized client');
      expect(error.name).to.equal('UnauthorizedClientError');
    });

    it('should have correct code', () => {
      const error = new UnauthorizedClientError('Unauthorized client');
      expect(error.code).to.equal('OAUTH_UNAUTHORIZED_CLIENT');
    });

    it('should have correct statusCode of 401', () => {
      const error = new UnauthorizedClientError('Unauthorized client');
      expect(error.statusCode).to.equal(401);
    });

    it('should preserve error message', () => {
      const error = new UnauthorizedClientError('Unauthorized client');
      expect(error.message).to.equal('Unauthorized client');
    });

    it('should be instanceof OAuthError', () => {
      const error = new UnauthorizedClientError('Unauthorized client');
      expect(error).to.be.an.instanceOf(OAuthError);
    });

    it('should be instanceof BaseError', () => {
      const error = new UnauthorizedClientError('Unauthorized client');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { clientId: 'unregistered-client' };
      const error = new UnauthorizedClientError('Unauthorized client', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new UnauthorizedClientError('Unauthorized client');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new UnauthorizedClientError('Unauthorized client');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'UnauthorizedClientError');
      expect(json).to.have.property('code', 'OAUTH_UNAUTHORIZED_CLIENT');
      expect(json).to.have.property('statusCode', 401);
    });
  });

  describe('ServerError', () => {
    it('should have correct name', () => {
      const error = new ServerError('Server error');
      expect(error.name).to.equal('ServerError');
    });

    it('should have correct code', () => {
      const error = new ServerError('Server error');
      expect(error.code).to.equal('OAUTH_SERVER_ERROR');
    });

    it('should have correct statusCode of 500', () => {
      const error = new ServerError('Server error');
      expect(error.statusCode).to.equal(500);
    });

    it('should preserve error message', () => {
      const error = new ServerError('Server error');
      expect(error.message).to.equal('Server error');
    });

    it('should be instanceof OAuthError', () => {
      const error = new ServerError('Server error');
      expect(error).to.be.an.instanceOf(OAuthError);
    });

    it('should be instanceof BaseError', () => {
      const error = new ServerError('Server error');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { internalCode: 'ERR_PROVIDER_DOWN' };
      const error = new ServerError('Server error', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new ServerError('Server error');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new ServerError('Server error');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'ServerError');
      expect(json).to.have.property('code', 'OAUTH_SERVER_ERROR');
      expect(json).to.have.property('statusCode', 500);
    });
  });
});
