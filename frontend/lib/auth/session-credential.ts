/**
 * Where the session's refresh token lives (ADR-WEB-02), so no script on the page can read it.
 *
 * - Web: an HttpOnly cookie the server sets at sign-in. The page never holds
 *   the token; it asks `/refresh/token` for an access token and the browser
 *   sends the cookie.
 * - Desktop: the app's pages are `app://`, another site from the server, so
 *   the main process keeps the token sealed by the OS keychain and hands it
 *   to the refresh call.
 *
 * The access token is kept in memory only (`auth-store.ts`). What localStorage
 * keeps is `pipeshub_signed_in`, which account this browser is signed in as:
 * not a credential, it lets a reload skip the refresh call when nobody is
 * signed in and lets the other tabs follow a sign-in or sign-out.
 */
import { isElectron } from '@/lib/electron';
import { getApiBaseUrl } from '@/lib/utils/api-base-url';

export const SESSION_HINT_KEY = 'pipeshub_signed_in';
/** Where builds before ADR-WEB-02 kept the tokens; read once to move them, then deleted. */
export const LEGACY_ACCESS_TOKEN_KEY = 'jwt_access_token';
export const LEGACY_REFRESH_TOKEN_KEY = 'jwt_refresh_token';

export const REFRESH_TOKEN_ENDPOINT = '/api/v1/userAccount/refresh/token';
export const SIGN_OUT_ENDPOINT = '/api/v1/userAccount/logout/manual';
export const SESSION_DELIVERY_HEADER = 'x-pipeshub-session';

/**
 * The desktop's keychain vault (electron/session-vault.ts). `key` names one of several sessions (an edition with a
 * session per organisation keys them by its id); without it, the app's one session.
 */
export interface DesktopSessionBridge {
  readRefreshToken(key?: string): Promise<string | null>;
  writeRefreshToken(refreshToken: string, key?: string): Promise<boolean>;
  clearRefreshToken(key?: string): Promise<boolean>;
  refreshTokenKeys(): Promise<string[]>;
  clearAllRefreshTokens(): Promise<boolean>;
}

function desktopVault(): DesktopSessionBridge | null {
  if (typeof window === 'undefined' || !isElectron()) return null;
  return (window as unknown as { electronAPI?: { session?: DesktopSessionBridge } }).electronAPI?.session ?? null;
}

/** The desktop's vault, or null on the web: for an edition that keeps several sessions in it. */
export function desktopSessionVault(): DesktopSessionBridge | null {
  return desktopVault();
}

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch {
    return null;
  }
}

/** Sent with a sign-in: the web app asks for its refresh token as a cookie; the desktop keeps the body. */
export function sessionDeliveryHeaders(): Record<string, string> {
  return isElectron() ? {} : { [SESSION_DELIVERY_HEADER]: 'cookie' };
}

/** `userId:orgId` of a JWT, read without checking it, or null. */
export function accountOfToken(token: string | null | undefined): string | null {
  try {
    const part = token?.split('.')[1];
    if (!part) return null;
    const payload = JSON.parse(atob(part.replace(/-/g, '+').replace(/_/g, '/'))) as Record<string, unknown>;
    return typeof payload.userId === 'string' ? `${payload.userId}:${String(payload.orgId ?? '')}` : null;
  } catch {
    return null;
  }
}

export function signedInMark(): string | null {
  return storage()?.getItem(SESSION_HINT_KEY) ?? null;
}

export function markSignedIn(accessToken: string): void {
  const value = accountOfToken(accessToken) ?? 'signed-in';
  try {
    if (storage()?.getItem(SESSION_HINT_KEY) !== value) storage()?.setItem(SESSION_HINT_KEY, value);
  } catch {
    // Private mode: the session still works in this tab.
  }
}

export function clearSignedInMark(): void {
  try {
    storage()?.removeItem(SESSION_HINT_KEY);
  } catch {
    // ignore
  }
}

export function readLegacyTokens(): { accessToken: string | null; refreshToken: string | null } {
  const s = storage();
  return {
    accessToken: s?.getItem(LEGACY_ACCESS_TOKEN_KEY) || null,
    refreshToken: s?.getItem(LEGACY_REFRESH_TOKEN_KEY) || null,
  };
}

export function clearLegacyTokens(): void {
  try {
    storage()?.removeItem(LEGACY_ACCESS_TOKEN_KEY);
    storage()?.removeItem(LEGACY_REFRESH_TOKEN_KEY);
    // The zustand-persist blob of an even older build.
    storage()?.removeItem('auth-storage');
  } catch {
    // ignore
  }
}

export type AccessTokenAnswer =
  /** `refreshToken`: the replacement a bearer refresh is answered with (the cookie path never sees one). */
  | { outcome: 'issued'; accessToken: string; refreshToken?: string }
  /** The server said no (signed out, expired, no session): sign in again. */
  | { outcome: 'refused' }
  /** No answer (offline, server down): the session may still be good. */
  | { outcome: 'unreachable' };

async function postRefresh(init: RequestInit): Promise<AccessTokenAnswer> {
  let response: Response;
  try {
    response = await fetch(`${getApiBaseUrl()}${REFRESH_TOKEN_ENDPOINT}`, {
      method: 'POST',
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init.headers as Record<string, string> | undefined) },
    });
  } catch {
    return { outcome: 'unreachable' };
  }
  if (response.status >= 500) return { outcome: 'unreachable' };
  if (!response.ok) return { outcome: 'refused' };
  try {
    const data = (await response.json()) as { accessToken?: unknown; refreshToken?: unknown };
    if (typeof data.accessToken !== 'string' || !data.accessToken) return { outcome: 'refused' };
    return typeof data.refreshToken === 'string' && data.refreshToken
      ? { outcome: 'issued', accessToken: data.accessToken, refreshToken: data.refreshToken }
      : { outcome: 'issued', accessToken: data.accessToken };
  } catch {
    return { outcome: 'refused' };
  }
}

/** Held across every tab and window of this origin while a refresh is out (ADR-WEB-02 D9). */
export const REFRESH_LOCK_NAME = 'pipeshub-session-refresh';

/**
 * Each refresh replaces the refresh token, and a replaced token presented again ends the session. Two tabs refreshing
 * at once would present the same token; the lock makes the second wait, so it sends the replacement the first got (the
 * cookie the browser now holds, or the desktop's vault). The server's grace covers a browser without the lock.
 */
async function oneRefreshAtATime<T>(work: () => Promise<T>): Promise<T> {
  const locks = typeof navigator !== 'undefined' ? navigator.locks : undefined;
  if (!locks || typeof locks.request !== 'function') return work();
  return locks.request(REFRESH_LOCK_NAME, { mode: 'exclusive' }, work) as Promise<T>;
}

/** Sends a refresh with the desktop's sealed token and seals the replacement before another window may read it. */
async function desktopRefresh(vault: DesktopSessionBridge): Promise<AccessTokenAnswer> {
  let refreshToken: string | null = null;
  try {
    refreshToken = await vault.readRefreshToken();
  } catch {
    return { outcome: 'unreachable' };
  }
  if (!refreshToken) return { outcome: 'refused' };
  const answer = await postRefresh({ headers: { Authorization: `Bearer ${refreshToken}` } });
  if (answer.outcome === 'issued' && answer.refreshToken) {
    await vault.writeRefreshToken(answer.refreshToken).catch(() => false);
  }
  return answer;
}

/**
 * An edition whose web app keeps its own session (one per signed-in workspace) names the refresh call's credential:
 * the headers to send, or null when the page holds no session. Unset, the cookie (web) or the keychain (desktop).
 * It may answer later (a desktop edition reading its keyed vault); it is read under the refresh lock either way.
 */
export type EditionRefreshCredential = () => Record<string, string> | null | Promise<Record<string, string> | null>;

/**
 * Where an edition keeps the refresh token that replaces the one its headers sent (ADR-WEB-02 D9): every refresh
 * rotates it, and the old one presented after a minute ends that session. Called before the next refresh may start.
 */
export type EditionRefreshKeeper = (refreshToken: string) => void | Promise<void>;

let editionRefreshCredential: EditionRefreshCredential | null = null;
let editionRefreshKeeper: EditionRefreshKeeper | null = null;

/**
 * Called once by an edition's auth store as it loads; null restores the default. An edition that sends a bearer
 * refresh token passes `keep`, which receives each replacement; one whose headers ask for the cookie
 * (`x-pipeshub-session: cookie`) gets the replacement as the cookie and needs none.
 */
export function installEditionRefreshCredential(
  credential: EditionRefreshCredential | null,
  keep: EditionRefreshKeeper | null = null,
): void {
  editionRefreshCredential = credential;
  editionRefreshKeeper = credential ? keep : null;
}

/** The edition's refresh: its headers are read under the lock, so they carry the replacement the last refresh kept. */
async function editionRefresh(credential: EditionRefreshCredential): Promise<AccessTokenAnswer> {
  let headers: Record<string, string> | null;
  try {
    headers = await credential();
  } catch {
    return { outcome: 'unreachable' };
  }
  if (!headers) return { outcome: 'refused' };
  // Credentials too: an edition whose headers ask for the cookie (naming the org, `x-pipeshub-session-org`) is
  // answered from it, and the from-source split is another origin.
  const answer = await postRefresh({ credentials: 'include', headers });
  if (answer.outcome === 'issued' && answer.refreshToken) {
    if (editionRefreshKeeper) {
      try {
        await editionRefreshKeeper(answer.refreshToken);
      } catch (error) {
        console.error('The edition could not keep the renewed session token:', error);
      }
    } else {
      console.error('The session token was renewed but the edition installed no keeper: the next refresh will end it');
    }
  }
  return answer;
}

/** A new access token from the session's refresh token, wherever it is kept. */
export async function requestAccessToken(): Promise<AccessTokenAnswer> {
  const edition = editionRefreshCredential;
  const vault = edition ? null : desktopVault();
  return oneRefreshAtATime(() => {
    if (edition) return editionRefresh(edition);
    return vault ? desktopRefresh(vault) : postRefresh({ credentials: 'include' });
  });
}

/**
 * Keeps a refresh token the page was handed (the desktop's sign-in, an older
 * build's localStorage, a sign-in flow that answers in the body): sealed on
 * the desktop, moved into the cookie on the web. On the web the answer also
 * carries a fresh access token.
 */
export async function keepRefreshToken(refreshToken: string): Promise<AccessTokenAnswer> {
  const vault = desktopVault();
  if (vault) {
    try {
      await vault.writeRefreshToken(refreshToken);
    } catch {
      return { outcome: 'unreachable' };
    }
    return requestAccessToken();
  }
  return oneRefreshAtATime(() =>
    postRefresh({
      credentials: 'include',
      headers: { Authorization: `Bearer ${refreshToken}`, [SESSION_DELIVERY_HEADER]: 'cookie' },
    }),
  );
}

/** Keeps a refresh token a sign-in answered with; unlike `keepRefreshToken`, asks for no access token on the desktop. */
export async function rememberRefreshToken(refreshToken: string): Promise<void> {
  const vault = desktopVault();
  if (vault) {
    await vault.writeRefreshToken(refreshToken).catch(() => undefined);
    return;
  }
  await keepRefreshToken(refreshToken);
}

/** Forgets the refresh token on this device. On the web the sign-out answer clears the cookie. */
export function forgetRefreshToken(): void {
  void desktopVault()?.clearRefreshToken().catch(() => undefined);
}

/**
 * Ends the session on the server, so a copy of the tokens stops working and
 * the cookie is cleared. With no access token left, the cookie alone signs out.
 */
export function endServerSession(accessToken: string | null): Promise<void> {
  if (!accessToken && desktopVault()) return Promise.resolve();
  return fetch(`${getApiBaseUrl()}${SIGN_OUT_ENDPOINT}`, {
    method: 'POST',
    credentials: 'include',
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
    // The page may navigate away right after this; keepalive lets the request finish.
    keepalive: true,
  }).then(
    () => undefined,
    () => undefined,
  );
}
