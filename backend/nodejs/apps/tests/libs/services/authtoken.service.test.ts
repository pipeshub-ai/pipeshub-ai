import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import jwt from 'jsonwebtoken';
import { AuthTokenService } from '../../../src/libs/services/authtoken.service';
import { UnauthorizedError } from '../../../src/libs/errors/http.errors';
import { Logger } from '../../../src/libs/services/logger.service';

describe('AuthTokenService', () => {
  let service: AuthTokenService;
  const jwtSecret = 'test-jwt-secret-for-authtoken-tests!';
  const scopedJwtSecret = 'test-scoped-jwt-secret-for-tests!';

  before(() => {
    // Ensure Logger singleton exists
    Logger.getInstance({ service: 'test', level: 'error' });
  });

  beforeEach(() => {
    service = new AuthTokenService(jwtSecret, scopedJwtSecret);
  });

  describe('generateToken', () => {
    it('should generate a valid JWT token', () => {
      const payload = { userId: 'user1', orgId: 'org1' };
      const token = service.generateToken(payload);
      expect(token).to.be.a('string');
      expect(token.split('.')).to.have.length(3); // JWT has 3 parts
    });

    it('should generate token with default 7d expiry', () => {
      const payload = { userId: 'user1' };
      const token = service.generateToken(payload);
      const decoded = jwt.decode(token) as Record<string, any>;
      expect(decoded).to.have.property('exp');
      expect(decoded).to.have.property('iat');
      // 7 days = 604800 seconds
      expect(decoded.exp - decoded.iat).to.equal(604800);
    });

    it('should generate token with custom expiry', () => {
      const payload = { userId: 'user1' };
      const token = service.generateToken(payload, '1h');
      const decoded = jwt.decode(token) as Record<string, any>;
      expect(decoded.exp - decoded.iat).to.equal(3600);
    });

    it('should include payload data in the token', () => {
      const payload = { userId: 'user1', orgId: 'org1', role: 'admin' };
      const token = service.generateToken(payload);
      const decoded = jwt.decode(token) as Record<string, any>;
      expect(decoded.userId).to.equal('user1');
      expect(decoded.orgId).to.equal('org1');
      expect(decoded.role).to.equal('admin');
    });
  });

  describe('generateScopedToken', () => {
    it('should generate a valid scoped JWT token', () => {
      const payload = { userId: 'user1', scopes: ['token:refresh'] };
      const token = service.generateScopedToken(payload);
      expect(token).to.be.a('string');
      expect(token.split('.')).to.have.length(3);
    });

    it('should generate scoped token with default 1h expiry', () => {
      const payload = { userId: 'user1', scopes: ['send:mail'] };
      const token = service.generateScopedToken(payload);
      const decoded = jwt.decode(token) as Record<string, any>;
      expect(decoded.exp - decoded.iat).to.equal(3600);
    });

    it('should generate scoped token with custom expiry', () => {
      const payload = { userId: 'user1', scopes: ['send:mail'] };
      const token = service.generateScopedToken(payload, '10m');
      const decoded = jwt.decode(token) as Record<string, any>;
      expect(decoded.exp - decoded.iat).to.equal(600);
    });

    it('should use a different secret than regular tokens', () => {
      const payload = { userId: 'user1' };
      const regularToken = service.generateToken(payload);
      const scopedToken = service.generateScopedToken(payload);
      // They should be different because they use different secrets
      expect(regularToken).to.not.equal(scopedToken);
    });
  });

  describe('verifyToken', () => {
    it('should verify a valid token and return decoded payload', async () => {
      const payload = { userId: 'user1', orgId: 'org1' };
      const token = service.generateToken(payload);
      const decoded = await service.verifyToken(token);
      expect(decoded.userId).to.equal('user1');
      expect(decoded.orgId).to.equal('org1');
    });

    it('should throw UnauthorizedError for an invalid token', async () => {
      try {
        await service.verifyToken('invalid.token.here');
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(UnauthorizedError);
      }
    });

    it('should throw UnauthorizedError for an expired token', async () => {
      const payload = { userId: 'user1' };
      const token = service.generateToken(payload, '-1s'); // already expired
      try {
        await service.verifyToken(token);
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(UnauthorizedError);
      }
    });

    it('should throw UnauthorizedError for token signed with wrong secret', async () => {
      const token = jwt.sign({ userId: 'user1' }, 'wrong-secret');
      try {
        await service.verifyToken(token);
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(UnauthorizedError);
      }
    });

    it('should not verify scoped tokens as regular tokens', async () => {
      const payload = { userId: 'user1', scopes: ['token:refresh'] };
      const scopedToken = service.generateScopedToken(payload);
      try {
        await service.verifyToken(scopedToken);
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(UnauthorizedError);
      }
    });
  });

  describe('verifyScopedToken', () => {
    it('should verify a valid scoped token with matching scope', async () => {
      const payload = { userId: 'user1', scopes: ['token:refresh', 'send:mail'] };
      const token = service.generateScopedToken(payload);
      const decoded = await service.verifyScopedToken(token, 'token:refresh');
      expect(decoded.userId).to.equal('user1');
      expect(decoded.scopes).to.include('token:refresh');
    });

    it('should throw UnauthorizedError for non-matching scope', async () => {
      const payload = { userId: 'user1', scopes: ['token:refresh'] };
      const token = service.generateScopedToken(payload);
      try {
        await service.verifyScopedToken(token, 'send:mail');
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(UnauthorizedError);
        expect((error as UnauthorizedError).message).to.equal('Invalid scope');
      }
    });

    it('should throw UnauthorizedError when token has no scopes', async () => {
      const payload = { userId: 'user1' }; // no scopes field
      const token = service.generateScopedToken(payload);
      try {
        await service.verifyScopedToken(token, 'token:refresh');
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(UnauthorizedError);
        expect((error as UnauthorizedError).message).to.equal('Invalid scope');
      }
    });

    it('should throw UnauthorizedError for invalid scoped token', async () => {
      try {
        await service.verifyScopedToken('invalid.token.here', 'token:refresh');
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(UnauthorizedError);
      }
    });

    it('should throw UnauthorizedError for expired scoped token', async () => {
      const payload = { userId: 'user1', scopes: ['token:refresh'] };
      const token = service.generateScopedToken(payload, '-1s');
      try {
        await service.verifyScopedToken(token, 'token:refresh');
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(UnauthorizedError);
      }
    });

    it('should not verify regular tokens as scoped tokens', async () => {
      const payload = { userId: 'user1', scopes: ['token:refresh'] };
      const regularToken = service.generateToken(payload);
      try {
        await service.verifyScopedToken(regularToken, 'token:refresh');
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(UnauthorizedError);
      }
    });
  });
});
