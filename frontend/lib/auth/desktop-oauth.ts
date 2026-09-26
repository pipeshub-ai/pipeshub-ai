/**
 * Shared vocabulary for desktop (Electron) sign-in.
 *
 * Both halves of the flow import this: the sign-in buttons running under the
 * `app://` origin, and the OAuth callback pages, which during a desktop
 * sign-in run in the user's ordinary browser at the server origin. It must
 * therefore stay free of any Electron import.
 */

export const DEEP_LINK_SCHEME = 'pipeshub';

/**
 * The backend's own configured frontend URL, learned from the unauthenticated
 * initAuth response before any sign-in button renders.
 *
 * The desktop app cannot derive this. It knows only the API base URL the user
 * typed, and the two are different origins whenever the UI is served
 * separately from the API, as it is when running from source. The value also
 * has to match exactly, because the backend redeems GitHub's code against its
 * own copy of it.
 */
let backendFrontendOrigin: string | null = null;

export function rememberFrontendOrigin(url: string | null | undefined): void {
  if (typeof url !== 'string') return;
  const trimmed = url.trim().replace(/\/+$/, '');
  if (!/^https?:\/\//i.test(trimmed)) return;
  backendFrontendOrigin = trimmed;
}

export function getFrontendOrigin(): string | null {
  return backendFrontendOrigin;
}

export type DesktopOAuthProvider = 'google' | 'microsoft' | 'github';

/**
 * Marks a `state` as belonging to a desktop flow, so the callback page knows
 * to hand the result to the app instead of posting it to an opener.
 *
 * A prefix rather than base64-encoded JSON, which `oauth-sign-in-button.tsx`
 * uses: `btoa` emits `+`, `/` and `=`, which survive the provider round trip
 * only while every hop re-encodes them. There is nothing structured to carry
 * here anyway, since the provider is implied by which callback page is running.
 */
const DESKTOP_STATE_PREFIX = 'phd.';

export function makeDesktopState(random: string): string {
  return `${DESKTOP_STATE_PREFIX}${random}`;
}

export function isDesktopOAuthState(state: string | null | undefined): boolean {
  return typeof state === 'string' && state.startsWith(DESKTOP_STATE_PREFIX);
}

/**
 * Build the link that carries the result back to the desktop app.
 *
 * Everything goes in the query string even though Google and Microsoft answer
 * in the fragment: a fragment is not reliably preserved across the browser to
 * OS-handler to process-argv hop, and the app never sees what is dropped.
 */
export function buildDesktopDeepLink(
  provider: DesktopOAuthProvider,
  params: Record<string, string | null | undefined>,
): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) search.set(key, value);
  }
  return `${DEEP_LINK_SCHEME}://auth/${provider}/callback?${search.toString()}`;
}
