import { LicenseError } from './license-codec';
import { webOrigin, type LicensePayload } from './license.types';

/** The web address users open: sign-in redirects, SSO and links are built from it. */
export const PUBLIC_URL_ENV = 'FRONTEND_PUBLIC_URL';

/**
 * Why this license may not run on this server, or null. A pro license names
 * the web address users open and runs only where FRONTEND_PUBLIC_URL is that
 * address (scheme, host and port); a trial or development license may leave the address out.
 */
export function licenseAddressProblem(
  payload: Pick<LicensePayload, 'type' | 'url'>,
  env: Readonly<Record<string, string | undefined>> = process.env,
): LicenseError | null {
  if (payload.url === undefined) {
    return payload.type === 'trial' || payload.type === 'development'
      ? null
      : new LicenseError('ADDRESS_REQUIRED');
  }
  const here = webOrigin(env[PUBLIC_URL_ENV]);
  // No address in the message: it reaches the client, and the licensed address is not theirs to read back.
  return here !== null && webOrigin(payload.url) === here
    ? null
    : new LicenseError('WRONG_ADDRESS');
}
