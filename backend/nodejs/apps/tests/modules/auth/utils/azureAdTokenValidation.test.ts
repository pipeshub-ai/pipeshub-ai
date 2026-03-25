import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import jwt from 'jsonwebtoken';
import axios from 'axios';
import {
  validateAzureAdUser,
  handleAzureAuthCallback,
} from '../../../../src/modules/auth/utils/azureAdTokenValidation';
import {
  BadRequestError,
  UnauthorizedError,
} from '../../../../src/libs/errors/http.errors';

describe('azureAdTokenValidation', () => {
  afterEach(() => {
    sinon.restore();
  });

  describe('validateAzureAdUser', () => {
    it('should throw BadRequestError when idToken is missing', async () => {
      try {
        await validateAzureAdUser({}, 'tenant123');
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError);
        expect((error as BadRequestError).message).to.equal(
          'Id token is required',
        );
      }
    });

    it('should throw BadRequestError when credentials are empty', async () => {
      try {
        await validateAzureAdUser({ idToken: undefined }, 'tenant123');
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError);
      }
    });

    it('should throw UnauthorizedError when token structure is invalid', async () => {
      // jwt.decode returns null for non-JWT strings
      sinon.stub(jwt, 'decode').returns(null);

      try {
        await validateAzureAdUser(
          { idToken: 'invalid-token', accessToken: 'access123' },
          'tenant123',
        );
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(UnauthorizedError);
        expect((error as UnauthorizedError).message).to.equal(
          'Invalid token structure',
        );
      }
    });

    it('should throw when handleAzureAuthCallback returns null but source does not await it', async () => {
      // Create a fake decoded token
      const fakeDecoded = {
        header: { kid: 'key123', alg: 'RS256' },
        payload: { email: 'user@test.com' },
      };
      sinon.stub(jwt, 'decode').returns(fakeDecoded as any);

      try {
        // accessToken missing => handleAzureAuthCallback returns Promise<null>
        // But the source does not await it, so the Promise (truthy) skips the
        // BadRequestError check and proceeds to the axios.get call, which fails
        await validateAzureAdUser(
          { idToken: 'some.id.token' },
          'tenant123',
        );
        expect.fail('Should have thrown');
      } catch (error: any) {
        // The error comes from the axios.get call to Microsoft's OpenID endpoint
        expect(error).to.be.an('error');
      }
    });
  });

  describe('handleAzureAuthCallback', () => {
    it('should return null when accessToken is missing', async () => {
      const result = await handleAzureAuthCallback(
        {},
        { payload: { email: 'test@test.com' } },
      );
      expect(result).to.be.null;
    });

    it('should return accessToken for non-JWT access tokens (personal accounts)', async () => {
      const credentials = { accessToken: 'opaque-token-value' };
      const decoded = { payload: { email: 'test@test.com' } };

      const result = await handleAzureAuthCallback(credentials, decoded);
      expect(result).to.equal('opaque-token-value');
    });

    it('should return accessToken for valid JWT access token with UPN', async () => {
      // Create a real JWT for the access token that jwt.decode can parse
      const accessPayload = { upn: 'user@company.com', name: 'User' };
      const fakeJwt = jwt.sign(accessPayload, 'fake-secret');

      const credentials = { accessToken: fakeJwt };
      const decoded = { payload: { email: 'user@company.com' } };

      const result = await handleAzureAuthCallback(credentials, decoded);
      expect(result).to.equal(fakeJwt);
    });

    it('should return null when JWT access token has no UPN and no fallback email', async () => {
      // JWT with no upn or email
      const accessPayload = { name: 'User' };
      const fakeJwt = jwt.sign(accessPayload, 'fake-secret');

      const credentials = {
        accessToken: fakeJwt,
        account: {},
      };
      const decoded = { payload: {} };

      const result = await handleAzureAuthCallback(credentials, decoded);
      expect(result).to.be.null;
    });

    it('should use decoded payload email as fallback for UPN', async () => {
      const accessPayload = { name: 'User' }; // no upn
      const fakeJwt = jwt.sign(accessPayload, 'fake-secret');

      const credentials = { accessToken: fakeJwt };
      const decoded = { payload: { email: 'fallback@company.com' } };

      const result = await handleAzureAuthCallback(credentials, decoded);
      expect(result).to.equal(fakeJwt);
    });

    it('should use account username as fallback when no UPN or email', async () => {
      const accessPayload = { name: 'User' }; // no upn
      const fakeJwt = jwt.sign(accessPayload, 'fake-secret');

      const credentials = {
        accessToken: fakeJwt,
        account: { username: 'account@company.com' },
      };
      const decoded = { payload: {} };

      const result = await handleAzureAuthCallback(credentials, decoded);
      expect(result).to.equal(fakeJwt);
    });
  });
});
