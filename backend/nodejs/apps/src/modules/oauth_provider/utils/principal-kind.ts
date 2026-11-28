import { SERVICE_TOKEN_APP_CLIENT_ID_PREFIX } from '../constants/constants';

/**
 * Who is behind a verified OAuth access token, read from how it was minted.
 *
 * - `human`: a personal access token, an authorization-code or device-code
 *   token, or a delegated token, which speaks as the person in `userId`.
 * - `app`: a client_credentials token (`userId` is the client id). No person.
 * - `service_account`: a service account's own token. No person.
 * - `unknown`: a shape no minting path here produces. Refused.
 */
export type OAuthPrincipalKind =
  'human' | 'app' | 'service_account' | 'unknown';

/** The claims the kind is read from; everything else on the token is ignored. */
export interface PrincipalClaims {
  userId?: unknown;
  client_id?: unknown;
  act?: unknown;
}

function isDelegation(
  claims: PrincipalClaims,
  serviceClient: boolean,
): boolean {
  const { act, userId } = claims;
  if (!serviceClient || typeof act !== 'object' || act === null) {
    return false;
  }
  const actor = (act as { sub?: unknown }).sub;
  return (
    typeof actor === 'string' &&
    actor !== '' &&
    typeof userId === 'string' &&
    userId !== '' &&
    userId !== actor
  );
}

/**
 * Classifies by provenance, never by comparing identity fields alone.
 *
 * Service tokens and the delegated tokens exchanged from them are the only
 * tokens minted against the org's `svc-system:` client (no OAuth app can be
 * registered under that prefix), and `act` is set only on a delegated token.
 * So a `svc-system:` token without `act` is the service account itself, even
 * though its `userId` names an account rather than the client.
 *
 * The Python services apply the same rules (`app/api/middlewares/principal_kind.py`);
 * `tests/modules/oauth_provider/fixtures/minted-token-shapes.json` is the
 * contract both are tested against.
 */
export function oauthPrincipalKind(
  claims: PrincipalClaims,
): OAuthPrincipalKind {
  const clientId = typeof claims.client_id === 'string' ? claims.client_id : '';
  const serviceClient = clientId.startsWith(SERVICE_TOKEN_APP_CLIENT_ID_PREFIX);
  if (claims.act !== undefined) {
    return isDelegation(claims, serviceClient) ? 'human' : 'unknown';
  }
  if (serviceClient) {
    return 'service_account';
  }
  if (
    typeof claims.userId === 'string' &&
    claims.userId !== '' &&
    claims.userId === clientId
  ) {
    return 'app';
  }
  return 'human';
}
