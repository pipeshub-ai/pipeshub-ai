const STORAGE_KEY = 'PIPESHUB_API_BASE_URL';

/**
 * Detect if the app is running inside Electron.
 * Checks both the preload-exposed flag and the user-agent string.
 */
export function isElectron(): boolean {
  if (typeof window === 'undefined') return false;
  return (
    !!(window as any).electronAPI?.isElectron ||
    navigator.userAgent.toLowerCase().includes('electron')
  );
}

/**
 * Returns the API base URL.
 *
 * - **Electron**: reads the user-configured URL from localStorage (set on first launch).
 * - **Web**: returns the build-time NEXT_PUBLIC_API_BASE_URL env variable. The
 *   web build never trusts localStorage — a stale value or any localStorage
 *   write from this origin would otherwise silently redirect every API call,
 *   including auth-bearing requests.
 */
export function getApiBaseUrl(): string {
  if (isElectron()) {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) return stored;
  }
  return process.env.NEXT_PUBLIC_API_BASE_URL || '';
}

/**
 * Persist the API base URL chosen by the user (Electron flow).
 */
export function setApiBaseUrl(url: string): void {
  localStorage.setItem(STORAGE_KEY, url);
}

/**
 * Whether an API base URL has been configured in localStorage.
 * Only meaningful inside Electron — the web build does not consult localStorage.
 */
export function hasStoredApiBaseUrl(): boolean {
  if (!isElectron()) return false;
  return !!localStorage.getItem(STORAGE_KEY);
}

export { STORAGE_KEY as API_BASE_URL_STORAGE_KEY };
