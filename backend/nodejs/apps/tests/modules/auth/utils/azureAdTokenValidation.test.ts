import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import jwt from 'jsonwebtoken';
import axios from 'axios';
import { generateKeyPairSync, KeyObject } from 'crypto';
import {
  MICROSOFT_CONSUMER_TENANT_ID,
  microsoftAccountIdentity,
  validateAzureAdUser,
  MICROSOFT_SIGN_IN_FAILED,
} from '../../../../src/modules/auth/utils/azureAdTokenValidation';
import {
  BadRequestError,
  ServiceUnavailableError,
  UnauthorizedError,
} from '../../../../src/libs/errors/http.errors';

const CLIENT_ID = 'pipeshub-client-id';
const OUR_TENANT = '11111111-1111-1111-1111-111111111111';
const OTHER_TENANT = '22222222-2222-2222-2222-222222222222';
const KID = 'test-key';

// Microsoft signs every tenant's tokens with the same keys; one key pair
// stands in for them here.
const { privateKey, publicKey } = generateKeyPairSync('rsa', {
  modulusLength: 2048,
});
const jwk = { ...(publicKey as KeyObject).export({ format: 'jwk' }), kid: KID };

const issuerFor = (tenant: string) =>
  `https://login.microsoftonline.com/${tenant}/v2.0`;

const sign = (claims: Record<string, unknown>, options: jwt.SignOptions = {}) =>
  jwt.sign(
    { tid: OUR_TENANT, iss: issuerFor(OUR_TENANT), aud: CLIENT_ID, ...claims },
    privateKey,
    { algorithm: 'RS256', keyid: KID, expiresIn: '5m', ...options },
  );

/** Serve the OpenID configuration Microsoft publishes for `tenant`. */
const serveMicrosoft = (tenant: string) => {
  const specific = !['common', 'organizations', 'consumers'].includes(tenant);
  const issuer = specific
    ? issuerFor(tenant)
    : tenant === 'consumers'
      ? issuerFor(MICROSOFT_CONSUMER_TENANT_ID)
      : 'https://login.microsoftonline.com/{tenantid}/v2.0';
  sinon.stub(axios, 'get').callsFake(async (url: string) => {
    if (url.endsWith('/.well-known/openid-configuration')) {
      return { data: { issuer, jwks_uri: 'https://keys.example/keys' } } as any;
    }
    return { data: { keys: [jwk] } } as any;
  });
};

const expectRejected = async (
  promise: Promise<unknown>,
  type: Function = UnauthorizedError,
) => {
  try {
    await promise;
    expect.fail('Should have been rejected');
  } catch (error) {
    expect(error).to.be.instanceOf(type);
    expect((error as Error).message).to.not.match(
      /jwt|aud|iss|tid|token structure/i,
    );
  }
};

describe('azureAdTokenValidation', () => {
  afterEach(() => {
    sinon.restore();
  });

  describe('validateAzureAdUser', () => {
    it('accepts a token issued for this app by the configured tenant', async () => {
      serveMicrosoft(OUR_TENANT);
      const claims = await validateAzureAdUser(
        { idToken: sign({ email: 'user@ours.test' }) },
        { clientId: CLIENT_ID, tenantId: OUR_TENANT },
      );
      expect(claims.email).to.equal('user@ours.test');
    });

    it('rejects a token issued for a different app', async () => {
      serveMicrosoft(OUR_TENANT);
      await expectRejected(
        validateAzureAdUser(
          { idToken: sign({ aud: 'someone-elses-app' }) },
          { clientId: CLIENT_ID, tenantId: OUR_TENANT },
        ),
      );
    });

    it('rejects a token from another tenant when the tenant is pinned', async () => {
      serveMicrosoft(OUR_TENANT);
      await expectRejected(
        validateAzureAdUser(
          {
            idToken: sign({ tid: OTHER_TENANT, iss: issuerFor(OTHER_TENANT) }),
          },
          { clientId: CLIENT_ID, tenantId: OUR_TENANT },
        ),
      );
    });

    it('rejects a token whose issuer does not match its own tenant', async () => {
      serveMicrosoft('common');
      await expectRejected(
        validateAzureAdUser(
          { idToken: sign({ tid: OTHER_TENANT, iss: issuerFor(OUR_TENANT) }) },
          { clientId: CLIENT_ID, tenantId: 'common' },
        ),
      );
    });

    it('accepts any tenant for a multi-tenant configuration', async () => {
      serveMicrosoft('common');
      const claims = await validateAzureAdUser(
        { idToken: sign({ tid: OTHER_TENANT, iss: issuerFor(OTHER_TENANT) }) },
        { clientId: CLIENT_ID, tenantId: 'common' },
      );
      expect(claims.tid).to.equal(OTHER_TENANT);
    });

    it('rejects personal accounts when only organizations are allowed', async () => {
      serveMicrosoft('organizations');
      await expectRejected(
        validateAzureAdUser(
          {
            idToken: sign({
              tid: MICROSOFT_CONSUMER_TENANT_ID,
              iss: issuerFor(MICROSOFT_CONSUMER_TENANT_ID),
            }),
          },
          { clientId: CLIENT_ID, tenantId: 'organizations' },
        ),
      );
    });

    it('rejects an expired token with a plain message', async () => {
      serveMicrosoft(OUR_TENANT);
      try {
        await validateAzureAdUser(
          { idToken: sign({}, { expiresIn: -10 }) },
          { clientId: CLIENT_ID, tenantId: OUR_TENANT },
        );
        expect.fail('Should have been rejected');
      } catch (error) {
        expect(error).to.be.instanceOf(UnauthorizedError);
        expect((error as Error).message).to.equal(
          'Your Microsoft sign-in expired. Please sign in again.',
        );
      }
    });

    it('rejects a token signed with an unknown key', async () => {
      serveMicrosoft(OUR_TENANT);
      await expectRejected(
        validateAzureAdUser(
          { idToken: sign({}, { keyid: 'unknown' }) },
          { clientId: CLIENT_ID, tenantId: OUR_TENANT },
        ),
      );
    });

    it('bounds the wait on Microsoft and asks the user to retry', async () => {
      const get = sinon.stub(axios, 'get').rejects(
        Object.assign(new Error('timeout of 5000ms exceeded'), {
          code: 'ECONNABORTED',
        }),
      );
      try {
        await validateAzureAdUser(
          { idToken: sign({}) },
          { clientId: CLIENT_ID, tenantId: OUR_TENANT },
        );
        expect.fail('Should have been rejected');
      } catch (error) {
        expect(error).to.be.instanceOf(ServiceUnavailableError);
        expect((error as Error).message).to.equal(
          "We couldn't reach Microsoft to check your sign-in. Please try again in a moment.",
        );
      }
      expect(get.firstCall.args[1]).to.deep.equal({ timeout: 5000 });
    });

    it('bounds the wait on the signing keys as well', async () => {
      const get = sinon.stub(axios, 'get');
      get.onFirstCall().resolves({
        data: {
          issuer: issuerFor(OUR_TENANT),
          jwks_uri: 'https://keys.example/keys',
        },
      } as any);
      get.onSecondCall().rejects(new Error('socket hang up'));
      await expectRejected(
        validateAzureAdUser(
          { idToken: sign({}) },
          { clientId: CLIENT_ID, tenantId: OUR_TENANT },
        ),
        ServiceUnavailableError,
      );
      expect(get.secondCall.args[1]).to.deep.equal({ timeout: 5000 });
    });

    it('asks for a sign-in retry when the ID token is missing', async () => {
      try {
        await validateAzureAdUser(
          {},
          { clientId: CLIENT_ID, tenantId: OUR_TENANT },
        );
        expect.fail('Should have been rejected');
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError);
        expect((error as Error).message).to.equal(MICROSOFT_SIGN_IN_FAILED);
      }
    });

    it('refuses to run without a configured client ID', async () => {
      try {
        await validateAzureAdUser(
          { idToken: sign({}) },
          { tenantId: OUR_TENANT },
        );
        expect.fail('Should have been rejected');
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError);
        expect((error as Error).message).to.include('Ask your admin');
      }
    });
  });

  describe('microsoftAccountIdentity', () => {
    it('trusts the email claim when the tenant is pinned', () => {
      const identity = microsoftAccountIdentity(
        {
          tid: OUR_TENANT,
          email: 'Mail@Ours.test',
          preferred_username: 'upn@ours.test',
        },
        OUR_TENANT,
      );
      expect(identity).to.deep.equal({
        email: 'mail@ours.test',
        emailClaimTrusted: true,
      });
    });

    it('ignores an email claim from another tenant in multi-tenant mode', () => {
      const identity = microsoftAccountIdentity(
        {
          tid: OTHER_TENANT,
          email: 'member@ours.test',
          preferred_username: 'other@elsewhere.test',
        },
        'common',
      );
      expect(identity).to.deep.equal({
        email: 'other@elsewhere.test',
        emailClaimTrusted: false,
      });
    });

    it('trusts the email claim when Microsoft verified its domain', () => {
      const identity = microsoftAccountIdentity(
        {
          tid: OTHER_TENANT,
          email: 'user@partner.test',
          preferred_username: 'upn@partner.test',
          xms_edov: true,
        },
        'common',
      );
      expect(identity.email).to.equal('user@partner.test');
      expect(identity.emailClaimTrusted).to.be.true;
    });

    it('trusts the email of a personal Microsoft account', () => {
      const identity = microsoftAccountIdentity(
        { tid: MICROSOFT_CONSUMER_TENANT_ID, email: 'someone@outlook.test' },
        'common',
      );
      expect(identity).to.deep.equal({
        email: 'someone@outlook.test',
        emailClaimTrusted: true,
      });
    });

    it('falls back to the UPN when there is no usable email', () => {
      const identity = microsoftAccountIdentity(
        {
          tid: OUR_TENANT,
          upn: 'UPN@ours.test',
          preferred_username: 'not-an-email',
        },
        OUR_TENANT,
      );
      expect(identity.email).to.equal('upn@ours.test');
    });
  });
});
