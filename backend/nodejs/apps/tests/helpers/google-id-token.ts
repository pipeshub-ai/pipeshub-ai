import sinon from 'sinon';
import { OAuth2Client } from 'google-auth-library';

/** Fakes Google's key fetch and returns the stub of the token check, to set what the token says. */
export const stubGoogleIdTokenCheck = () => {
  sinon
    .stub(OAuth2Client.prototype, 'getFederatedSignonCertsAsync')
    .resolves({ certs: {} } as Awaited<ReturnType<OAuth2Client['getFederatedSignonCertsAsync']>>);
  return sinon.stub(OAuth2Client.prototype, 'verifySignedJwtWithCertsAsync');
};
