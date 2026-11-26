import { expect } from 'chai';
import nock from 'nock';
import sinon from 'sinon';
import jwt from 'jsonwebtoken';
import { generateKeyPairSync } from 'crypto';
import { inspect } from 'util';
import { OAuth2Client } from 'google-auth-library';
import {
  GOOGLE_SIGN_IN_FAILED,
  verifyGoogleIdToken,
} from '../../../../src/modules/auth/utils/googleIdTokenValidation';
import { refusalReason } from '../../../../src/libs/services/audit';
import { HttpError, WrongCredentialError } from '../../../../src/libs/errors/http.errors';

const CLIENT = 'google-client';
const keyPair = () => generateKeyPairSync('rsa', { modulusLength: 2048, publicKeyEncoding: { type: 'spki', format: 'pem' }, privateKeyEncoding: { type: 'pkcs8', format: 'pem' } });
const google = keyPair();
const other = keyPair();
const idToken = (key: string, claims: Record<string, unknown> = {}, keyid = 'g1') =>
  jwt.sign({ iss: 'https://accounts.google.com', aud: CLIENT, email: 'someone@acme.test', ...claims }, key, {
    algorithm: 'RS256', keyid, ...('exp' in claims ? {} : { expiresIn: '5m' }),
  });
// An hour ago: far beyond the library's 300 s clock skew.
const anHourAgo = () => Math.floor(Date.now() / 1000) - 3600;
const inAnHour = () => Math.floor(Date.now() / 1000) + 3600;
const segment = (value: unknown) => Buffer.from(JSON.stringify(value)).toString('base64url');
const certs = () => nock('https://www.googleapis.com').get('/oauth2/v1/certs');

describe('verifyGoogleIdToken', () => {
  before(() => nock.disableNetConnect());
  after(() => nock.enableNetConnect());
  afterEach(() => {
    sinon.restore();
    nock.cleanAll();
  });

  it('returns the ticket of a token signed by Google for this client', async () => {
    certs().reply(200, { g1: google.publicKey });
    const ticket = await verifyGoogleIdToken(CLIENT, idToken(google.privateKey));
    expect(ticket.getPayload()?.email).to.equal('someone@acme.test');
  });

  for (const [name, token, cause] of [
    ['signed with another key', () => idToken(other.privateKey), /^Invalid token signature/],
    ['for another client', () => idToken(google.privateKey, { aud: 'someone-else' }), /^Wrong recipient/],
    ['expired', () => idToken(google.privateKey, { iat: anHourAgo() - 300, exp: anHourAgo() }), /^Token used too late/],
    ['signed with a key Google does not list', () => idToken(google.privateKey, {}, 'g2'), /^No pem found/],
    ['not a JWT', () => 'not-a-token', /^Wrong number of segments/],
    // An opaque access token sent in the ID-token field: the library quotes it whole, and no log redaction rule knows its shape.
    ['that is an opaque access token', () => 'ya29.a0AfB_opaqueAccessToken1234567890', /^Wrong number of segments/],
    // Client garbage the library fails to parse throws a SyntaxError: still the caller's wrong token.
    ['with an unparsable header', () => 'a.b.c', /^Can't parse token envelope/],
    // The library quotes the header segment here without a ': ' before it.
    ['with an unparsable payload', () => `${segment({ alg: 'RS256', kid: 'g1' })}.%%%.c2ln`, /^Can't parse token payload$/],
    ['with an ES256 signature of the wrong length', () => `${segment({ alg: 'ES256', kid: 'g1' })}.${segment({})}.c2ln`, /^"ES256" signatures must be$/],
    ['issued in the future', () => idToken(google.privateKey, { iat: inAnHour(), exp: inAnHour() + 600 }), /^Token used too early$/],
    ['valid for more than a day', () => idToken(google.privateKey, { exp: inAnHour() + 2 * 86400 }), /^Expiration time too far in future$/],
    ['from another issuer', () => idToken(google.privateKey, { iss: 'https://issuer.test' }), /^Invalid issuer$/],
    // The key lookup turns `kid` into a property key, which throws for this object.
    ['whose key id is not a key', () => `${segment({ alg: 'RS256', kid: { toString: 1, valueOf: 1 } })}.${segment({})}.c2ln`, /^Cannot convert object to primitive value$/],
  ] as const) {
    it(`refuses a token ${name} as a wrong credential, keeping the library's reason, without the token or its claims, as the cause`, async () => {
      certs().reply(200, { g1: google.publicKey });
      const submitted = token();
      const error = await verifyGoogleIdToken(CLIENT, submitted).catch((e: unknown) => e);
      expect(error).to.be.an.instanceOf(WrongCredentialError);
      expect(error).to.include({ statusCode: 401, message: GOOGLE_SIGN_IN_FAILED });
      expect(refusalReason(error)).to.equal('sso_token_invalid');
      const libraryError = (error as Error).cause;
      expect(libraryError).to.be.an.instanceOf(Error);
      expect((libraryError as Error).message).to.match(cause);
      const chain = inspect(error, { depth: null });
      for (const part of [submitted, ...submitted.split('.').filter((p) => p.length > 1), 'someone@acme.test']) {
        expect(chain).to.not.include(part);
      }
    });
  }

  for (const [name, body] of [
    ['an empty key set', {}],
    ['a 200 whose body is not JSON (a maintenance page)', 'Down for maintenance'],
  ] as const) {
    it(`${name} from Google is our failure, not the caller's token: untagged, the token never checked`, async () => {
      certs().reply(200, body);
      const check = sinon.spy(OAuth2Client.prototype, 'verifySignedJwtWithCertsAsync');
      const error = await verifyGoogleIdToken(CLIENT, idToken(google.privateKey)).catch((e: unknown) => e);
      expect(error).to.be.an.instanceOf(Error);
      expect(error).to.not.be.an.instanceOf(HttpError);
      expect((error as Error).message).to.equal('Google served no ID token signing keys');
      expect(refusalReason(error)).to.equal(undefined);
      expect(check.called).to.equal(false);
    });
  }

  it("a key Google served that crypto can't read is our failure, not the caller's token: rethrown untagged", async () => {
    certs().reply(200, { g1: '-----BEGIN CERTIFICATE-----\nnot-a-certificate\n-----END CERTIFICATE-----\n' });
    const error = await verifyGoogleIdToken(CLIENT, idToken(google.privateKey)).catch((e: unknown) => e);
    expect(error).to.be.an.instanceOf(Error);
    expect(error).to.not.be.an.instanceOf(HttpError);
    expect((error as Error).message).to.match(/DECODER/);
    expect(refusalReason(error)).to.equal(undefined);
  });

  it('a check that throws something other than an Error is not called a refusal: rethrown as is', async () => {
    certs().reply(200, { g1: google.publicKey });
    const thrown = { reason: 'not an Error' };
    sinon.stub(OAuth2Client.prototype, 'verifySignedJwtWithCertsAsync').rejects(thrown);
    const error = await verifyGoogleIdToken(CLIENT, idToken(google.privateKey)).catch((e: unknown) => e);
    expect(error).to.equal(thrown);
    expect(refusalReason(error)).to.equal(undefined);
  });

  it('a non-string token is our bug, not a refusal: an untagged TypeError, before any key fetch', async () => {
    const fetch = sinon
      .stub(OAuth2Client.prototype, 'getFederatedSignonCertsAsync')
      .resolves({ certs: { g1: google.publicKey } } as Awaited<ReturnType<OAuth2Client['getFederatedSignonCertsAsync']>>);
    const error = await verifyGoogleIdToken(CLIENT, { credential: idToken(google.privateKey) }).catch((e: unknown) => e);
    expect(error).to.be.an.instanceOf(TypeError);
    expect(error).to.not.be.an.instanceOf(HttpError);
    expect(refusalReason(error)).to.equal(undefined);
    expect(fetch.called).to.equal(false);
  });

  // Stubbed: the library retries a failed key fetch for seconds.
  it("leaves the error untagged when Google's keys can't be fetched", async () => {
    const outage = new Error('Failed to retrieve verification certificates: connect ECONNREFUSED');
    sinon.stub(OAuth2Client.prototype, 'getFederatedSignonCertsAsync').rejects(outage);
    const check = sinon.spy(OAuth2Client.prototype, 'verifySignedJwtWithCertsAsync');
    const error = await verifyGoogleIdToken(CLIENT, idToken(google.privateKey)).catch((e: unknown) => e);
    expect(error).to.equal(outage);
    expect(refusalReason(error)).to.equal(undefined);
    expect(check.called).to.equal(false);
  });
});
