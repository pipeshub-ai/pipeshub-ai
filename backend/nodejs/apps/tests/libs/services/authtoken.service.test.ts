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
  // Verification keys are derived once in the constructor instead of letting
  // jsonwebtoken re-parse the secret on every call. Signing still passes the
  // raw string, so every test above already proves the HS256 round trip
  // survived that change. The asymmetric branch is what nothing else reaches.
  describe('PEM verification keys', () => {
    const { generateKeyPairSync } = require('node:crypto');

    const makeKeyPair = () =>
      generateKeyPairSync('rsa', {
        modulusLength: 2048,
        publicKeyEncoding: { type: 'spki', format: 'pem' },
        privateKeyEncoding: { type: 'pkcs8', format: 'pem' },
      });

    it('should verify a token signed with the matching private key', async () => {
      const { publicKey, privateKey } = makeKeyPair();
      const pemService = new AuthTokenService(publicKey, scopedJwtSecret);
      const token = jwt.sign({ userId: 'user1' }, privateKey, {
        algorithm: 'RS256',
        expiresIn: '1h',
      });

      const decoded = await pemService.verifyToken(token);
      expect(decoded.userId).to.equal('user1');
    });

    it('should reject a token signed with a different private key', async () => {
      const { publicKey } = makeKeyPair();
      const other = makeKeyPair();
      const pemService = new AuthTokenService(publicKey, scopedJwtSecret);
      const token = jwt.sign({ userId: 'user1' }, other.privateKey, {
        algorithm: 'RS256',
        expiresIn: '1h',
      });

      try {
        await pemService.verifyToken(token);
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(UnauthorizedError);
      }
    });

    it('should accept a PEM secret for scoped tokens too', async () => {
      const { publicKey, privateKey } = makeKeyPair();
      const pemService = new AuthTokenService(jwtSecret, publicKey);
      const token = jwt.sign({ userId: 'user1', scopes: ['token:refresh'] }, privateKey, {
        algorithm: 'RS256',
        expiresIn: '1h',
      });

      const decoded = await pemService.verifyScopedToken(token, 'token:refresh');
      expect(decoded.userId).to.equal('user1');
    });
  });
  // verify() rejects a falsy secret before it coerces the key, so an empty
  // secret used to fail closed. Deriving the KeyObject at construction skips
  // that check, and a token forged with an empty HMAC key then verifies.
  describe('empty secret', () => {
    const { createHmac } = require('node:crypto');

    const forgeWithEmptyKey = (payload: Record<string, unknown>) => {
      const b64 = (o: unknown) =>
        Buffer.from(JSON.stringify(o)).toString('base64url');
      const head = b64({ alg: 'HS256', typ: 'JWT' });
      const body = b64({ ...payload, exp: Math.floor(Date.now() / 1000) + 3600 });
      const sig = createHmac('sha256', Buffer.from(''))
        .update(`${head}.${body}`)
        .digest('base64url');
      return `${head}.${body}.${sig}`;
    };

    it('should refuse to construct with an empty jwt secret', () => {
      expect(() => new AuthTokenService('', scopedJwtSecret)).to.throw(
        /JWT secret is not configured/,
      );
    });

    it('should refuse to construct with an empty scoped secret', () => {
      expect(() => new AuthTokenService(jwtSecret, '')).to.throw(
        /JWT secret is not configured/,
      );
    });

    it('should not accept a token forged with an empty HMAC key', async () => {
      const forged = forgeWithEmptyKey({ userId: 'attacker', orgId: 'victim' });
      try {
        await service.verifyToken(forged);
        expect.fail('Forged token was accepted');
      } catch (error) {
        expect(error).to.be.instanceOf(UnauthorizedError);
      }
    });
  });

  // A token's own `alg` header must not steer verification.
  describe('algorithm pinning', () => {
    it('should reject an unsigned (alg=none) token', async () => {
      const b64 = (o: unknown) =>
        Buffer.from(JSON.stringify(o)).toString('base64url');
      const unsigned =
        `${b64({ alg: 'none', typ: 'JWT' })}.${b64({ userId: 'attacker' })}.`;
      try {
        await service.verifyToken(unsigned);
        expect.fail('Unsigned token was accepted');
      } catch (error) {
        expect(error).to.be.instanceOf(UnauthorizedError);
      }
    });
  });
});
