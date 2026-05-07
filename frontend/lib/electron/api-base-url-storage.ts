import { isElectron } from './is-electron';

export const API_BASE_URL_STORAGE_KEY = 'PIPESHUB_API_BASE_URL';

/**
 * Persist the API base URL chosen by the user (Electron flow).
 */
export function setApiBaseUrl(url: string): void {
  localStorage.setItem(API_BASE_URL_STORAGE_KEY, url);
}

/**
 * Whether an API base URL has been configured in localStorage.
 * Only meaningful inside Electron — the web build does not consult localStorage.
 */
export function hasStoredApiBaseUrl(): boolean {
  if (!isElectron()) return false;
  return !!localStorage.getItem(API_BASE_URL_STORAGE_KEY);
}
