import { expect } from 'chai';
import nock from 'nock';
import sinon from 'sinon';
import jwt from 'jsonwebtoken';
import { generateKeyPairSync } from 'crypto';
import { OAuth2Client } from 'google-auth-library';
import { verifyGoogleIdToken } from '../../../../src/modules/auth/utils/googleIdTokenValidation';
import { refusalReason } from '../../../../src/libs/services/audit';

const CLIENT = 'google-client';
const keyPair = () => generateKeyPairSync('rsa', { modulusLength: 2048, publicKeyEncoding: { type: 'spki', format: 'pem' }, privateKeyEncoding: { type: 'pkcs8', format: 'pem' } });
const google = keyPair();
const other = keyPair();
const idToken = (key: string, claims: Record<string, unknown> = {}) =>
  jwt.sign({ iss: 'https://accounts.google.com', aud: CLIENT, email: 'someone@acme.test', ...claims }, key, { algorithm: 'RS256', keyid: 'g1', expiresIn: '5m' });
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

  for (const [name, token] of [
    ['signed with another key', () => idToken(other.privateKey)],
    ['for another client', () => idToken(google.privateKey, { aud: 'someone-else' })],
    ['not a JWT', () => 'not-a-token'],
  ] as const) {
    it(`tags a token ${name} as sso_token_invalid and keeps the error as it was`, async () => {
      certs().reply(200, { g1: google.publicKey });
      const error = await verifyGoogleIdToken(CLIENT, token()).catch((e: unknown) => e);
      expect(error).to.be.an.instanceOf(Error);
      expect(Object.getPrototypeOf(error)).to.equal(Error.prototype);
      expect(refusalReason(error)).to.equal('sso_token_invalid');
    });
  }

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
