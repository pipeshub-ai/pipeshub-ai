import jwt, { JwtPayload } from 'jsonwebtoken';
import axios from 'axios';
import jwkToPem from 'jwk-to-pem';
import {
  BadRequestError,
  ServiceUnavailableError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { Logger } from '../../../libs/services/logger.service';
const logger = Logger.getInstance({
  service: 'Azure Ad Token Validation',
});

// Microsoft signs ID tokens for every tenant and app with the same keys, so a
// valid signature alone says nothing about who the token was issued for.
const MULTI_TENANT_AUTHORITIES = new Set([
  'common',
  'organizations',
  'consumers',
]);
// Personal Microsoft accounts (outlook.com, live.com, ...) all share this tenant.
export const MICROSOFT_CONSUMER_TENANT_ID =
  '9188040d-6c67-4c5b-b112-36a304b66dad';

// Sign-in waits on these two calls, so they get a short bound rather than the
// 10s used for background telemetry calls.
const MICROSOFT_METADATA_TIMEOUT_MS = 5000;

export const MICROSOFT_SIGN_IN_FAILED =
  "Sign-in with Microsoft didn't complete. Try again; if it keeps happening, ask your admin to check the Microsoft sign-in settings.";
const WRONG_ACCOUNT =
  "This Microsoft account can't be used to sign in here. Sign in with your organization's Microsoft account, or ask your admin which account to use.";

interface OpenIdConfiguration {
  issuer?: string;
  jwks_uri?: string;
}

interface JsonWebKeySet {
  keys?: (jwkToPem.JWK & { kid?: string })[];
}

export interface MicrosoftSignInConfig {
  clientId?: string;
  tenantId?: string;
}

export const isSingleTenantConfig = (tenantId?: string): boolean =>
  !MULTI_TENANT_AUTHORITIES.has((tenantId || 'common').trim().toLowerCase());

export const validateAzureAdUser = async (
  credentials: { idToken?: unknown },
  config: MicrosoftSignInConfig,
): Promise<JwtPayload> => {
  const idToken = credentials.idToken;
  if (typeof idToken !== 'string' || idToken === '') {
    throw new BadRequestError(MICROSOFT_SIGN_IN_FAILED);
  }
  const clientId = config.clientId?.trim();
  if (!clientId) {
    throw new BadRequestError(
      "Microsoft sign-in isn't fully set up. Ask your admin to add the application (client) ID in the Microsoft sign-in settings.",
    );
  }
  const tenant = (config.tenantId || 'common').trim();

  const decoded = jwt.decode(idToken, { complete: true });
  if (decoded === null) throw new UnauthorizedError(MICROSOFT_SIGN_IN_FAILED);

  let openIdConfig: { data: OpenIdConfiguration };
  let jwks: { data: JsonWebKeySet };
  try {
    openIdConfig = await axios.get<OpenIdConfiguration>(
      `https://login.microsoftonline.com/${encodeURIComponent(tenant)}/v2.0/.well-known/openid-configuration`,
      { timeout: MICROSOFT_METADATA_TIMEOUT_MS },
    );
    jwks = await axios.get<JsonWebKeySet>(openIdConfig.data.jwks_uri ?? '', {
      timeout: MICROSOFT_METADATA_TIMEOUT_MS,
    });
  } catch (error) {
    logger.warn('Could not reach Microsoft to check the sign-in', {
      reason: error instanceof Error ? error.message : String(error),
    });
    throw new ServiceUnavailableError(
      "We couldn't reach Microsoft to check your sign-in. Please try again in a moment.",
    );
  }

  const signingKey = jwks.data.keys?.find(
    (key) => key.kid === decoded.header.kid,
  );
  if (!signingKey) throw new UnauthorizedError(MICROSOFT_SIGN_IN_FAILED);

  let verified: JwtPayload;
  try {
    const result = jwt.verify(idToken, jwkToPem(signingKey), {
      algorithms: ['RS256'],
      audience: clientId,
    });
    if (typeof result === 'string') throw new UnauthorizedError(WRONG_ACCOUNT);
    verified = result;
  } catch (error) {
    if (error instanceof jwt.TokenExpiredError) {
      throw new UnauthorizedError(
        'Your Microsoft sign-in expired. Please sign in again.',
      );
    }
    logger.warn('Rejected a Microsoft ID token', {
      reason: error instanceof Error ? error.message : String(error),
    });
    throw new UnauthorizedError(WRONG_ACCOUNT);
  }

  // A tenant-specific configuration publishes its own issuer; the multi-tenant
  // ones publish a {tenantid} template that must match the token's own tid.
  const tid = typeof verified.tid === 'string' ? verified.tid : '';
  const expectedIssuer = (openIdConfig.data.issuer ?? '').replace(
    '{tenantid}',
    tid,
  );
  const allowedForAuthority =
    tenant.toLowerCase() !== 'organizations' ||
    tid !== MICROSOFT_CONSUMER_TENANT_ID;
  if (
    !tid ||
    !expectedIssuer ||
    verified.iss !== expectedIssuer ||
    !allowedForAuthority
  ) {
    logger.warn('Rejected a Microsoft ID token from an unexpected tenant', {
      tokenTenant: tid,
      configuredTenant: tenant,
    });
    throw new UnauthorizedError(WRONG_ACCOUNT);
  }

  return verified;
};

/**
 * The email that identifies the PipesHub account for a verified token, and
 * whether the token's `email` claim can be trusted to change stored data.
 *
 * `email` is set by tenant admins and never verified by Microsoft, so with a
 * multi-tenant configuration anyone's tenant could claim any address. It is
 * trusted only when the tenant is pinned, for personal accounts (Microsoft
 * owns those addresses), or when Microsoft vouches for the domain (xms_edov).
 * Otherwise the sign-in name is used: for work accounts it is the UPN, whose
 * domain must be verified by the tenant that issued it.
 */
export const microsoftAccountIdentity = (
  claims: JwtPayload,
  tenantId?: string,
): { email?: string; emailClaimTrusted: boolean } => {
  const emailClaimTrusted =
    isSingleTenantConfig(tenantId) ||
    claims.tid === MICROSOFT_CONSUMER_TENANT_ID ||
    claims.xms_edov === true ||
    claims.xms_edov === 'true' ||
    claims.xms_edov === '1';
  const signInName = [claims.preferred_username, claims.upn].find(
    (value): value is string =>
      typeof value === 'string' && value.includes('@'),
  );
  const email =
    (emailClaimTrusted && typeof claims.email === 'string' && claims.email) ||
    signInName;
  return { email: email ? email.toLowerCase() : undefined, emailClaimTrusted };
};
