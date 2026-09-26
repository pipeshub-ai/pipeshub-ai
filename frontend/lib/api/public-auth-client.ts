import axios from 'axios';
import { applyElectronOverrides, isElectron } from '@/lib/electron';
import { getFrontendOrigin, rememberFrontendOrigin } from '@/lib/auth/desktop-oauth';

/**
 * Unauthenticated axios instance for login/sign-up flows (no Bearer interceptors).
 * Session correlation uses `x-session-token` from initAuth, stored in sessionStorage.
 */
export const publicAuthClient = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_BASE_URL,
  timeout: 30000,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
});

publicAuthClient.interceptors.request.use(applyElectronOverrides);

/**
 * Identifies the desktop app on the calls whose response differs for it.
 *
 * `client-name` is the existing convention for this (the Slack bot sends
 * `slack`). Scoped to the callers that need it rather than set globally, so it
 * cannot change the behaviour of endpoints that already branch on this header.
 */
export function desktopClientConfig(): { headers: Record<string, string> } | undefined {
  return isElectron() ? { headers: { 'client-name': 'desktop' } } : undefined;
}

/** In flight request, so concurrent callers share one round trip. */
let frontendOriginRequest: Promise<void> | null = null;

/**
 * Fetch the server's configured frontend origin, once, for Electron only.
 *
 * The desktop app builds its OAuth redirect URI from this and cannot derive it,
 * because it knows only the API base URL the user typed and the two are
 * different origins whenever the UI is served separately. Callers await it
 * beside `initAuth` so the value is in hand before any provider button can
 * render. Fetching on click instead would put a round trip in front of the
 * browser opening, and a failure there would silently fall back to the API base
 * URL and produce a redirect URI mismatch.
 *
 * Resolves immediately on the web, where the request is never made at all.
 */
export function ensureDesktopFrontendOrigin(): Promise<void> {
  if (!isElectron() || getFrontendOrigin()) return Promise.resolve();
  if (!frontendOriginRequest) {
    frontendOriginRequest = publicAuthClient
      .get<{ frontendUrl?: string }>('/api/v1/userAccount/frontend-url', desktopClientConfig())
      .then((response) => {
        rememberFrontendOrigin(response.data?.frontendUrl);
      })
      .catch(() => {
        // A server predating this route has none. buildDesktopRedirectUri then
        // falls back to the API base URL, which is right wherever one process
        // serves both the API and the UI.
      })
      .finally(() => {
        frontendOriginRequest = null;
      });
  }
  return frontendOriginRequest;
}
