import { isElectron, getElectronApiBaseUrl } from '@/lib/electron';

/**
 * Returns the API base URL.
 *
 * - **Electron**: session URL (pre-login Continue) then durable localStorage (post-login).
 * - **Web**: returns the build-time NEXT_PUBLIC_API_BASE_URL env variable. The
 *   web build never trusts localStorage — a stale value or any localStorage
 *   write from this origin would otherwise silently redirect every API call,
 *   including auth-bearing requests.
 */
export function getApiBaseUrl(): string {
  if (isElectron()) {
    return getElectronApiBaseUrl();
  }
  return process.env.NEXT_PUBLIC_API_BASE_URL || '';
}
