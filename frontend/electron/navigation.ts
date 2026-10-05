/**
 * What the main window may navigate to.
 *
 * The renderer reads its server URL and tokens from `app://` storage. A
 * top-level navigation to the server's own web UI (a SAML redirect, a link, a
 * server-side redirect) keeps the preload bridge but loses that storage, so
 * every API call falls back to the page's own origin and sign-in degrades to
 * a password-only form. Such URLs belong in the user's browser instead.
 */

export function isAppUrl(rawUrl: string, scheme: string): boolean {
  try {
    return new URL(rawUrl).protocol === `${scheme}:`;
  } catch {
    return false;
  }
}

export function isExternalWebUrl(rawUrl: string): boolean {
  try {
    const { protocol } = new URL(rawUrl);
    return protocol === 'http:' || protocol === 'https:';
  } catch {
    return false;
  }
}
