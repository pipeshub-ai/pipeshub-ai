import { isElectron, API_BASE_URL_STORAGE_KEY } from '@/lib/electron';

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
    const stored = localStorage.getItem(API_BASE_URL_STORAGE_KEY);
    if (stored) return stored;
  }
  return process.env.NEXT_PUBLIC_API_BASE_URL || '';
}
