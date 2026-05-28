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

/** Session URL first (same-run Continue), then localStorage draft / post-login URL. */
export function getElectronApiBaseUrl(): string {
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
