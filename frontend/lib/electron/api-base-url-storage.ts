import { isElectron } from './is-electron';

const API_BASE_URL_STORAGE_KEY = 'PIPESHUB_API_BASE_URL';
const API_BASE_URL_SESSION_KEY = 'PIPESHUB_API_BASE_URL_SESSION';

/** Set on successful login; survives restarts. Cleared on explicit workspace logout. */
const SERVER_URL_LOGIN_ACK_KEY = 'PIPESHUB_SERVER_URL_LOGIN_ACK';

function getStoredApiBaseUrl(): string | null {
  if (typeof window === 'undefined') return null;
  return window.localStorage.getItem(API_BASE_URL_STORAGE_KEY);
}

function getSessionApiBaseUrl(): string | null {
  if (typeof window === 'undefined') return null;
  return window.sessionStorage.getItem(API_BASE_URL_SESSION_KEY);
}

export function setSessionApiBaseUrl(url: string): void {
  if (typeof window === 'undefined') return;
  window.sessionStorage.setItem(API_BASE_URL_SESSION_KEY, url);
  // Draft for pre-fill on reopen; login ack is set only after successful login.
  window.localStorage.setItem(API_BASE_URL_STORAGE_KEY, url);
}

function setApiBaseUrl(url: string): void {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(API_BASE_URL_STORAGE_KEY, url);
}

function hasServerUrlSetupAck(): boolean {
  if (typeof window === 'undefined') return false;
  return window.localStorage.getItem(SERVER_URL_LOGIN_ACK_KEY) === '1';
}

function setServerUrlSetupAck(): void {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(SERVER_URL_LOGIN_ACK_KEY, '1');
}

function clearSessionServerUrlState(): void {
  if (typeof window === 'undefined') return;
  window.sessionStorage.removeItem(API_BASE_URL_SESSION_KEY);
}

function clearServerUrlSetupAck(): void {
  if (typeof window === 'undefined') return;
  window.localStorage.removeItem(SERVER_URL_LOGIN_ACK_KEY);
}

/** Workspace logout (Electron): clear ack + session; keep last persisted URL for pre-fill. */
export function clearElectronLogoutServerState(): void {
  if (typeof window === 'undefined') return;
  clearServerUrlSetupAck();
  clearSessionServerUrlState();
}

/** Leaving local mode: its loopback URL must not be offered, or reused, as a server. */
export function forgetElectronServerUrl(): void {
  if (typeof window === 'undefined') return;
  clearServerUrlSetupAck();
  clearSessionServerUrlState();
  window.localStorage.removeItem(API_BASE_URL_STORAGE_KEY);
}

/**
 * Local mode's URL: the launcher picks a new port at each start, so it is kept in memory for this launch and never saved.
 * `unknown` until the app knows its mode; `local` with no URL while this launch's server is not ready yet.
 */
type ApiBaseUrlMode = { kind: 'unknown' } | { kind: 'server' } | { kind: 'local'; url: string | null };
let mode: ApiBaseUrlMode = { kind: 'unknown' };
let waiters: (() => void)[] = [];
const urlListeners = new Set<(url: string) => void>();

/** Called whenever local mode's URL changes: '' when this launch's server goes away, then its URL when it is back. */
export function onApiBaseUrlChange(listener: (url: string) => void): () => void {
  urlListeners.add(listener);
  return () => urlListeners.delete(listener);
}

function settle(): void {
  if (mode.kind === 'unknown' || (mode.kind === 'local' && !mode.url)) return;
  const ready = waiters;
  waiters = [];
  ready.forEach((resolve) => resolve());
}

/** Local mode: `url` is this launch's server once it reports ready, null before. Never written to storage. */
export function setLocalModeApiBaseUrl(url: string | null): void {
  const before = getElectronApiBaseUrl();
  mode = { kind: 'local', url };
  settle();
  const now = url ?? '';
  if (now !== before) urlListeners.forEach((listener) => listener(now));
}

/** Connecting to a server: its saved URL applies. */
export function markServerMode(): void {
  mode = { kind: 'server' };
  settle();
}

/** Resolves once the base URL points at a server of this launch: at once outside Electron or in server mode. */
export function whenApiBaseUrlReady(): Promise<void> {
  if (!isElectron() || mode.kind === 'server' || (mode.kind === 'local' && mode.url)) return Promise.resolve();
  return new Promise((resolve) => waiters.push(resolve));
}

export function resetApiBaseUrlModeForTests(): void {
  mode = { kind: 'unknown' };
  waiters = [];
}

/** Local mode: this launch's URL only. Otherwise the session URL (same-run Continue), then the localStorage draft / post-login URL. */
export function getElectronApiBaseUrl(): string {
  if (mode.kind === 'local') return mode.url ?? '';
  return getSessionApiBaseUrl() ?? getStoredApiBaseUrl() ?? '';
}

export function shouldSkipElectronServerUrlSetup(): boolean {
  if (!isElectron()) return true;
  if (typeof window === 'undefined') return false;

  // Same session after Connect — skip until the app quits.
  if (getSessionApiBaseUrl()) return true;
  // After login — skip until workspace logout.
  return !!getStoredApiBaseUrl() && hasServerUrlSetupAck();
}

/** Promotes the draft URL to a logged-in session after successful login. */
export function persistElectronServerUrlOnLogin(): void {
  if (!isElectron() || typeof window === 'undefined') return;

  const sessionUrl = getSessionApiBaseUrl();
  if (sessionUrl) {
    setApiBaseUrl(sessionUrl);
    setServerUrlSetupAck();
    clearSessionServerUrlState();
    return;
  }

  if (getStoredApiBaseUrl()) {
    setServerUrlSetupAck();
  }
}
