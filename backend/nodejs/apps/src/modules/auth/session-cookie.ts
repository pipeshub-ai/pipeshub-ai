import type { CookieOptions, NextFunction, Request, Response } from 'express';
import jwt from 'jsonwebtoken';
import { BaseError } from '../../libs/errors/base.error';
import { ForbiddenError } from '../../libs/errors/http.errors';
import { hardenedProfile } from '../../libs/utils/security-profile';

/**
 * The browser session's refresh token travels in an HttpOnly cookie, never in
 * a response body the page's scripts can read (ADR-WEB-02).
 *
 * The cookie's name follows the page's scheme. A page served over https gets
 * the `__Host-` cookie (Secure, no Domain, Path=/), which no sibling subdomain
 * and no plain-http response can set or overwrite. A page served over plain
 * http (a laptop install, the from-source split on localhost:3001/3000, a LAN
 * install the installer warned about) cannot hold a Secure cookie in every
 * browser, so it gets a plain-named cookie without Secure, scoped to the
 * account routes. Each scheme reads only its own name, so a cookie planted over
 * http is never read by an https page.
 */
export const SECURE_SESSION_COOKIE = '__Host-pipeshub_refresh';
export const PLAIN_SESSION_COOKIE = 'pipeshub_refresh';
const PLAIN_SESSION_COOKIE_PATH = '/api/v1/userAccount/';

/** A browser asks for the cookie by sending this header with the value `cookie`. Other clients keep the body. */
export const SESSION_DELIVERY_HEADER = 'x-pipeshub-session';
export const SESSION_DELIVERY_COOKIE = 'cookie';

/**
 * A client that holds several organisations' sessions in one browser (an edition with a session per workspace) names
 * the organisation a refresh or sign-out is for. Each organisation's session then has a cookie of its own,
 * `<name>.<orgId>`, with the same attributes. Without the header a browser has one session, as above.
 */
export const SESSION_ORG_HEADER = 'x-pipeshub-session-org';
const SESSION_ORG = /^[A-Za-z0-9_-]{1,64}$/;

export const FOREIGN_ORIGIN_MESSAGE =
  'This request must come from a PipesHub page on this server';

const FROM_COOKIE = 'sessionFromCookie';
const ADOPT_INTO_COOKIE = 'sessionAdoptIntoCookie';
const SIGNED_OUT_TOKENS = 'sessionSignedOutTokens';

// Read through these: hand-built requests and responses (internal callers, tests) may come without headers or
// locals, whatever express's types say.
const headersOf = (req: Request): Request['headers'] | undefined => req.headers;
const localsOf = (res: Response): Record<string, unknown> | undefined =>
  res.locals;

type Env = Readonly<Record<string, string | undefined>>;

function originHeader(req: Request): string | null {
  const value = req.headers.origin;
  return typeof value === 'string' && value !== '' && value !== 'null'
    ? value
    : null;
}

/**
 * Whether the page that sent the request was served over https. The browser's
 * Origin says it even behind a TLS-terminating proxy the app does not trust;
 * without one, the connection decides.
 */
export function pageIsSecure(req: Request): boolean {
  const origin = originHeader(req);
  if (origin) return origin.startsWith('https://');
  const request: { secure?: boolean } = req;
  return request.secure === true;
}

/** The organisation the request names with `x-pipeshub-session-org`, or null. */
export function sessionOrgOfRequest(req: Request): string | null {
  const value = headersOf(req)?.[SESSION_ORG_HEADER];
  return typeof value === 'string' && SESSION_ORG.test(value) ? value : null;
}

/** The organisation a refresh token was issued for, read without checking it (it names a cookie, never a grant). */
export function sessionOrgOfToken(token: string): string | null {
  const decoded = jwt.decode(token) as { orgId?: unknown } | null;
  const org = decoded?.orgId;
  return typeof org === 'string' && SESSION_ORG.test(org) ? org : null;
}

function cookieNameFor(secure: boolean, org: string | null): string {
  const base = secure ? SECURE_SESSION_COOKIE : PLAIN_SESSION_COOKIE;
  return org ? `${base}.${org}` : base;
}

export function sessionCookieName(
  req: Request,
  org: string | null = sessionOrgOfRequest(req),
): string {
  return cookieNameFor(pageIsSecure(req), org);
}

function cookieOptions(secure: boolean): CookieOptions {
  return {
    httpOnly: true,
    secure,
    sameSite: 'strict',
    path: secure ? '/' : PLAIN_SESSION_COOKIE_PATH,
  };
}

export function parseCookieHeader(
  header: string | undefined,
): Map<string, string> {
  const cookies = new Map<string, string>();
  if (!header) return cookies;
  for (const part of header.split(';')) {
    const eq = part.indexOf('=');
    if (eq <= 0) continue;
    const name = part.slice(0, eq).trim();
    if (!name || cookies.has(name)) continue;
    let value = part.slice(eq + 1).trim();
    if (value.startsWith('"') && value.endsWith('"'))
      value = value.slice(1, -1);
    try {
      cookies.set(name, decodeURIComponent(value));
    } catch {
      cookies.set(name, value);
    }
  }
  return cookies;
}

/** The refresh token in this scheme's cookie, or null. */
export function readSessionCookie(req: Request): string | null {
  const value = parseCookieHeader(req.headers.cookie).get(
    sessionCookieName(req),
  );
  return value ? value : null;
}

export function wantsCookieDelivery(req: Request): boolean {
  const value = headersOf(req)?.[SESSION_DELIVERY_HEADER];
  return (
    typeof value === 'string' &&
    value.trim().toLowerCase() === SESSION_DELIVERY_COOKIE
  );
}

/** The origins that may use the cookie besides the server's own: the configured UI and CORS origins. */
export function trustedPageOrigins(env: Env = process.env): Set<string> {
  const origins = new Set<string>();
  const add = (raw: string | undefined): void => {
    if (!raw) return;
    try {
      origins.add(new URL(raw.trim()).origin);
    } catch {
      // A malformed entry trusts nothing.
    }
  };
  for (const entry of (env.ALLOWED_ORIGINS ?? 'http://localhost:3001').split(
    ',',
  ))
    add(entry);
  add(env.FRONTEND_PUBLIC_URL);
  return origins;
}

/**
 * The CSRF check for a request the cookie authenticates. SameSite=Strict keeps
 * other sites' requests from carrying the cookie; this also refuses a sibling
 * subdomain (same site, other origin) and a request with no Origin at all,
 * which no browser page sends on a POST.
 */
export function cookieRequestOriginAllowed(
  req: Request,
  env: Env = process.env,
): boolean {
  if (req.headers['sec-fetch-site'] === 'cross-site') return false;
  const origin = originHeader(req);
  if (!origin) return false;
  let url: URL;
  try {
    url = new URL(origin);
  } catch {
    return false;
  }
  if (trustedPageOrigins(env).has(url.origin)) return true;
  const host = req.headers.host;
  return typeof host === 'string' && url.host === host.toLowerCase();
}

function remainingLifetimeMs(token: string): number | undefined {
  const decoded = jwt.decode(token) as { exp?: unknown } | null;
  if (!decoded || typeof decoded.exp !== 'number') return undefined;
  const remaining = decoded.exp * 1000 - Date.now();
  return remaining > 0 ? remaining : undefined;
}

/**
 * Sets the session cookie for a page whose scheme the caller knows (a sign-in that arrives as an identity provider's
 * POST carries the provider's Origin, not the page's). `org` names an organisation's own cookie.
 */
export function writeSessionCookie(
  res: Response,
  refreshToken: string,
  scope: { secure: boolean; org: string | null },
): void {
  const maxAge = remainingLifetimeMs(refreshToken);
  res.cookie(cookieNameFor(scope.secure, scope.org), refreshToken, {
    ...cookieOptions(scope.secure),
    ...(maxAge !== undefined ? { maxAge } : {}),
  });
}

/**
 * `org` defaults to the token's organisation when the request names one (`x-pipeshub-session-org`), so a refresh
 * answers into the cookie it read; otherwise the browser's one cookie.
 */
export function setSessionCookie(
  req: Request,
  res: Response,
  refreshToken: string,
  org: string | null = sessionOrgOfRequest(req)
    ? sessionOrgOfToken(refreshToken)
    : null,
): void {
  writeSessionCookie(res, refreshToken, { secure: pageIsSecure(req), org });
}

/** The organisations whose own session cookie the request carries. */
export function sessionOrgsOfCookies(req: Request): string[] {
  const orgs = new Set<string>();
  for (const name of parseCookieHeader(headersOf(req)?.cookie).keys()) {
    for (const base of [SECURE_SESSION_COOKIE, PLAIN_SESSION_COOKIE]) {
      const org = name.startsWith(`${base}.`)
        ? name.slice(base.length + 1)
        : null;
      if (org && SESSION_ORG.test(org)) orgs.add(org);
    }
  }
  return [...orgs];
}

/** Clears both names: a person who moved the install from http to https signs out of both. */
export function clearSessionCookies(
  res: Response,
  orgs: Iterable<string | null> = [null],
): void {
  for (const org of orgs) {
    res.clearCookie(cookieNameFor(true, org), {
      httpOnly: true,
      secure: true,
      sameSite: 'strict',
      path: '/',
    });
    res.clearCookie(cookieNameFor(false, org), {
      httpOnly: true,
      sameSite: 'strict',
      path: PLAIN_SESSION_COOKIE_PATH,
    });
  }
}

export function sessionCameFromCookie(res: Response): boolean {
  return localsOf(res)?.[FROM_COOKIE] === true;
}

/**
 * A body-token client that asks for refresh-token rotation (ADR-WEB-02 D9) sends this header with 1, true or on: each
 * refresh then answers a `refreshToken` to keep in place of the one sent.
 */
export const REFRESH_ROTATION_HEADER = 'x-pipeshub-refresh-rotation';

/**
 * Whether a refresh from the body (not the cookie) rotates its token. Always on the hardened profile; on the
 * compatibility profile (ADR-OPS-07) only for a client that asks, so a script that reuses the token it was given at
 * sign-in keeps working, as before D9. A cookie session always rotates.
 */
export function refreshRotationWanted(
  req: Request,
  env: Env = process.env,
): boolean {
  if (hardenedProfile(env)) return true;
  const raw = req.headers[REFRESH_ROTATION_HEADER];
  const value = (Array.isArray(raw) ? raw[0] : raw)?.trim().toLowerCase() ?? '';
  return value === '1' || value === 'true' || value === 'on';
}

/** A bearer refresh token whose caller asked to move it into the cookie (the localStorage migration). */
export function adoptIntoCookieRequested(res: Response): boolean {
  return localsOf(res)?.[ADOPT_INTO_COOKIE] === true;
}

/**
 * Lets the refresh token come from the cookie. A bearer header wins and is
 * handled exactly as before (the desktop app, scripts, the SDK); the cookie is
 * read only when no header is sent, and then only from a page of this server.
 */
export function refreshTokenFromCookie(env: Env = process.env) {
  return (req: Request, res: Response, next: NextFunction): void => {
    if (req.headers.authorization) {
      if (wantsCookieDelivery(req)) res.locals[ADOPT_INTO_COOKIE] = true;
      next();
      return;
    }
    const token = readSessionCookie(req);
    if (!token) {
      next();
      return;
    }
    if (!cookieRequestOriginAllowed(req, env)) {
      next(new ForbiddenError(FOREIGN_ORIGIN_MESSAGE));
      return;
    }
    req.headers.authorization = `Bearer ${token}`;
    res.locals[FROM_COOKIE] = true;
    next();
  };
}

/** A cookie the server refused (expired, ended by a sign-out) is cleared, so the browser stops sending it. */
export function clearRefusedSessionCookie() {
  return (
    error: unknown,
    req: Request,
    res: Response,
    next: NextFunction,
  ): void => {
    if (
      sessionCameFromCookie(res) &&
      error instanceof BaseError &&
      error.statusCode === 401
    ) {
      clearSessionCookies(res, [sessionOrgOfRequest(req)]);
    }
    next(error);
  };
}

/**
 * Sign-out clears the cookie whatever else happens (every organisation's, as sign-out ends every session), and lets
 * a page whose access token has already gone sign out with the cookie alone.
 */
export function signOutSessionCookie(env: Env = process.env) {
  return (req: Request, res: Response, next: NextFunction): void => {
    const token = readSessionCookie(req);
    if (token && !req.headers.authorization) {
      if (!cookieRequestOriginAllowed(req, env)) {
        next(new ForbiddenError(FOREIGN_ORIGIN_MESSAGE));
        return;
      }
      req.headers.authorization = `Bearer ${token}`;
      res.locals[FROM_COOKIE] = true;
    }
    res.locals[SIGNED_OUT_TOKENS] = sessionCookieTokens(req);
    clearSessionCookies(res, [null, ...sessionOrgsOfCookies(req)]);
    next();
  };
}

/** Every session cookie value the request carries, of this scheme: the default one and each organisation's. */
export function sessionCookieTokens(req: Request): string[] {
  const cookies = parseCookieHeader(headersOf(req)?.cookie);
  const names = [null, ...sessionOrgsOfCookies(req)].map((org) =>
    sessionCookieName(req, org),
  );
  return [
    ...new Set(
      names.map((n) => cookies.get(n)).filter((v): v is string => !!v),
    ),
  ];
}

/**
 * R48MIGFIX-3: the refresh tokens a sign-out's cookies held, unchecked. The controller ends only the session families
 * among them that belong to the caller it authenticated, so a refresh answered just before the sign-out (whose new
 * cookie may land after the sign-out cleared it) leaves nothing that still works.
 */
export function signedOutSessionTokens(res: Response): string[] {
  const v = localsOf(res)?.[SIGNED_OUT_TOKENS];
  return Array.isArray(v) ? (v as string[]) : [];
}
