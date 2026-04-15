const STORAGE_KEY = 'pipeshub-api-base-url';

/**
 * Resolves the API base URL at runtime.
 * Priority: localStorage override > build-time env var.
 *
 * In the Tauri desktop app the env var is empty so the localStorage value
 * (set on the /setup page) is the only source. In web deployments the
 * env var is inlined at build time and used as the default.
 */
export function getApiBaseUrl(): string {
  if (typeof window !== 'undefined') {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) return stored;
  }
  return process.env.NEXT_PUBLIC_API_BASE_URL || '';
}

export function setApiBaseUrl(url: string): void {
  const normalized = url.replace(/\/+$/, '');
  localStorage.setItem(STORAGE_KEY, normalized);
}

export function clearApiBaseUrl(): void {
  localStorage.removeItem(STORAGE_KEY);
}

export function hasApiBaseUrl(): boolean {
  return !!getApiBaseUrl();
}

/**
 * Returns true only if the user has explicitly configured a server URL
 * via the /setup page (stored in localStorage). This ignores the
 * build-time env var, which may be baked in from the web build but is
 * not meaningful for the desktop app.
 */
export function hasStoredApiBaseUrl(): boolean {
  if (typeof window === 'undefined') return false;
  return !!localStorage.getItem(STORAGE_KEY);
}

/**
 * Returns true when the app is running inside a Tauri WebView.
 * Tauri v2 injects `__TAURI_INTERNALS__` on the window object.
 */
export function isTauri(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
}
