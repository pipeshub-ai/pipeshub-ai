import { getSafeReturnTo } from '@/lib/utils/safe-return-to';

/**
 * Where a sign-in goes when the address carries no `returnTo` (WP-54): kept in the tab's session storage, so it
 * survives an SSO/SAML round trip through the identity provider and a session that expired on the page (the app
 * then signs in at `/login` with no `returnTo`). A path only, never anything secret; used once.
 */
const KEY = 'pipeshub.postSignInReturnTo';

export function rememberReturnTo(path: string, win: Window = window): void {
  const safe = getSafeReturnTo(path);
  if (safe === null) return;
  try {
    win.sessionStorage.setItem(KEY, safe);
  } catch {
    // Storage refused (private mode): the sign-in falls back to its default.
  }
}

/** The remembered destination, left in place; null when none, or not a path inside the app. */
export function peekReturnTo(win: Window = window): string | null {
  try {
    return getSafeReturnTo(win.sessionStorage.getItem(KEY));
  } catch {
    return null;
  }
}

/** The remembered destination, forgotten as it is read; null when none, or not a path inside the app. */
export function takeReturnTo(win: Window = window): string | null {
  try {
    const remembered = win.sessionStorage.getItem(KEY);
    win.sessionStorage.removeItem(KEY);
    return getSafeReturnTo(remembered);
  } catch {
    return null;
  }
}

export function forgetReturnTo(win: Window = window): void {
  try {
    win.sessionStorage.removeItem(KEY);
  } catch {
    // Nothing kept.
  }
}
