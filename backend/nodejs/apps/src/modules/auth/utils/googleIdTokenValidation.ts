import { LoginTicket, OAuth2Client } from 'google-auth-library';
import { WrongCredentialError } from '../../../libs/errors/http.errors';
import { withCause } from '../../../libs/errors/base.error';
import { authRefusal } from './sign-in-audit';

export const GOOGLE_SIGN_IN_FAILED =
  "Sign-in with Google didn't complete. Try again, or use another sign-in method.";

// How google-auth-library 11.3.1 starts each refusal of the token itself. The
// rest of its message can quote the token or its claims (email, name), so only
// this fixed text is kept as the reason.
const TOKEN_REFUSALS = [
  'Wrong number of segments in token',
  "Can't parse token envelope",
  "Can't parse token payload",
  'No pem found for envelope',
  '"ES256" signatures must be',
  'Invalid token signature',
  'No issue time in token',
  'No expiration time in token',
  'iat field using invalid format',
  'exp field using invalid format',
  'Expiration time too far in future',
  'Token used too early',
  'Token used too late',
  'Invalid issuer',
  'Wrong recipient',
  // V8's TypeError when the check turns a token field (a header `kid`, a claim) into a key or number.
  'Cannot convert object to primitive value',
];

/**
 * `OAuth2Client.verifyIdToken`, run as its two steps so that only a token
 * Google's keys reject is a refusal: a 401 `WrongCredentialError`
 * (`sso_token_invalid`) whose cause is the library's reason, without the
 * token or claims its message quotes. A failure to fetch the keys, an empty
 * key set, a key Google served that crypto can't read, or a caller passing
 * something other than the token string, stays an untagged error (a 500).
 */
export const verifyGoogleIdToken = async (
  clientId: string,
  idToken: unknown,
): Promise<LoginTicket> => {
  if (typeof idToken !== 'string') {
    throw new TypeError('verifyGoogleIdToken needs the ID token string');
  }
  const client = new OAuth2Client(clientId);
  const { certs } = await client.getFederatedSignonCertsAsync();
  // Typed as a key map, but a non-JSON 200 body reaches us as a string. With no
  // keys every token would be refused as the caller's ("No pem found").
  const served: unknown = certs;
  if (
    typeof served !== 'object' ||
    served === null ||
    Array.isArray(served) ||
    Object.keys(served).length === 0
  ) {
    throw new Error('Google served no ID token signing keys');
  }
  try {
    return await client.verifySignedJwtWithCertsAsync(
      idToken,
      certs,
      clientId,
      client.issuers,
    );
  } catch (error) {
    const reason =
      error instanceof Error
        ? TOKEN_REFUSALS.find((refusal) => error.message.startsWith(refusal))
        : undefined;
    // Not a refusal of the token, such as an unreadable key from Google: our failure, a 500.
    if (reason === undefined) throw error;
    throw withCause(
      authRefusal(
        new WrongCredentialError(GOOGLE_SIGN_IN_FAILED),
        'sso_token_invalid',
      ),
      new Error(reason),
    );
  }
};
