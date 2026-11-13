import { LoginTicket, OAuth2Client } from 'google-auth-library';
import { authRefusal } from './sign-in-audit';

/**
 * `OAuth2Client.verifyIdToken`, run as its two steps so that only a token
 * Google's keys reject is a refusal (`sso_token_invalid`). A failure to fetch
 * the keys stays an untagged system error. The error itself is unchanged.
 */
export const verifyGoogleIdToken = async (
  clientId: string,
  idToken: string,
): Promise<LoginTicket> => {
  const client = new OAuth2Client(clientId);
  const { certs } = await client.getFederatedSignonCertsAsync();
  try {
    return await client.verifySignedJwtWithCertsAsync(
      idToken,
      certs,
      clientId,
      client.issuers,
    );
  } catch (error) {
    throw error instanceof Error
      ? authRefusal(error, 'sso_token_invalid')
      : error;
  }
};
