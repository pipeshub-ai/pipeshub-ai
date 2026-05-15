import { isElectron } from './is-electron';

export const API_BASE_URL_STORAGE_KEY = 'PIPESHUB_API_BASE_URL';

/** Set when the user completes the Electron server-URL screen; survives restarts. Cleared on explicit logout only. */
export const SERVER_URL_ACK_STORAGE_KEY = 'PIPESHUB_SERVER_URL_ACK_V1';

const LEGACY_LAUNCH_CONFIRM_KEY = 'PIPESHUB_URL_CONFIRMED_LAUNCH_ID';

/**
 * Persist the API base URL chosen by the user (Electron flow).
 */
export function setApiBaseUrl(url: string): void {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(API_BASE_URL_STORAGE_KEY, url);
}

/**
 * Whether an API base URL has been configured in localStorage.
 * Only meaningful inside Electron — the web build does not consult localStorage.
 */
export function hasStoredApiBaseUrl(): boolean {
  if (!isElectron()) return false;
  if (typeof window === 'undefined') return false;
  return !!window.localStorage.getItem(API_BASE_URL_STORAGE_KEY);
}

export function hasServerUrlSetupAck(): boolean {
  if (typeof window === 'undefined') return false;
  return window.localStorage.getItem(SERVER_URL_ACK_STORAGE_KEY) === '1';
}

export function setServerUrlSetupAck(): void {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(SERVER_URL_ACK_STORAGE_KEY, '1');
}

/** Cleared when the user logs out from the workspace menu (Electron) so the server URL step shows again. */
export function clearServerUrlSetupAck(): void {
  if (typeof window === 'undefined') return;
  window.localStorage.removeItem(SERVER_URL_ACK_STORAGE_KEY);
  window.localStorage.removeItem(LEGACY_LAUNCH_CONFIRM_KEY);
}

/** Workspace logout (Electron): wipe saved server URL and ack so the add-URL screen is empty. */
export function clearElectronLogoutServerState(): void {
  if (typeof window === 'undefined') return;
  window.localStorage.removeItem(API_BASE_URL_STORAGE_KEY);
  clearServerUrlSetupAck();
}

/**
 * One-time migration from the old per-process launch-id confirmation to a durable ack.
 */
export function migrateLegacyServerUrlConfirmation(): void {
  if (typeof window === 'undefined' || !isElectron()) return;
  if (hasServerUrlSetupAck()) return;
  if (!hasStoredApiBaseUrl()) return;
  setServerUrlSetupAck();
  window.localStorage.removeItem(LEGACY_LAUNCH_CONFIRM_KEY);
}
