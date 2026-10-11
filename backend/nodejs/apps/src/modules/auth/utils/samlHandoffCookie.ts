import { CookieOptions, Request, Response } from 'express';
import { HANDOFF_TTL_SECONDS } from '../services/samlDesktopHandoff.service';

export const SAML_HANDOFF_COOKIE = 'saml_handoff';
export const SAML_HANDOFF_COOKIE_PATH = '/api/v1/saml';

// Lax still rides the same-site exchange XHR, and the path keeps it off every other API call.
const handoffCookieOptions: CookieOptions = {
  httpOnly: true,
  secure: true,
  sameSite: 'lax',
  path: SAML_HANDOFF_COOKIE_PATH,
};

// Older releases bridged SAML tokens through these; a cookie is only removed
// when the clearing attributes match the ones it was set with.
const LEGACY_TOKEN_COOKIES = ['accessToken', 'refreshToken'];
const legacyTokenCookieOptions: CookieOptions = {
  path: '/',
  secure: true,
  sameSite: 'none',
};

export function setSamlHandoffCookie(res: Response, binder: string): void {
  res.cookie(SAML_HANDOFF_COOKIE, binder, {
    ...handoffCookieOptions,
    maxAge: HANDOFF_TTL_SECONDS * 1000,
  });
}

export function readSamlHandoffCookie(req: Request): string | null {
  const header = req.headers.cookie;
  if (header === undefined) return null;
  for (const pair of header.split(';')) {
    const separator = pair.indexOf('=');
    if (
      separator !== -1 &&
      pair.slice(0, separator).trim() === SAML_HANDOFF_COOKIE
    ) {
      return pair.slice(separator + 1).trim();
    }
  }
  return null;
}

export function clearSamlHandoffCookie(res: Response): void {
  res.clearCookie(SAML_HANDOFF_COOKIE, handoffCookieOptions);
}

export function clearLegacySamlTokenCookies(res: Response): void {
  for (const name of LEGACY_TOKEN_COOKIES) {
    res.clearCookie(name, legacyTokenCookieOptions);
  }
}
