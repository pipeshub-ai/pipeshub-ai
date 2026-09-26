/**
 * Deep links that carry an OAuth result back from the user's browser.
 *
 * The desktop app cannot run the OAuth screen itself: the renderer's origin is
 * `app://.`, which no provider will accept as a redirect target. Instead the
 * authorize URL opens in the default browser, the provider redirects to the
 * ordinary web callback page on the server origin, and that page forwards the
 * result here as `pipeshub://auth/<provider>/callback`.
 *
 * The OS hands us whatever any local caller passes, so parsing is a trust
 * boundary. Nothing here decides that a link is authentic — the renderer does
 * that by matching the `state` it generated. See lib/electron/oauth-deep-link.ts.
 */

export const DEEP_LINK_SCHEME = 'pipeshub';

const SUPPORTED_PROVIDERS = ['google', 'microsoft', 'github'] as const;

export type DeepLinkProvider = (typeof SUPPORTED_PROVIDERS)[number];

export interface OAuthDeepLink {
  provider: DeepLinkProvider;
  /** Merged query and fragment params: providers use one or the other. */
  params: Record<string, string>;
}

function isSupportedProvider(value: string): value is DeepLinkProvider {
  return (SUPPORTED_PROVIDERS as readonly string[]).includes(value);
}

/**
 * Parse `pipeshub://auth/<provider>/callback`, returning null for anything
 * that is not one of ours.
 */
export function parseOAuthDeepLink(rawUrl: string): OAuthDeepLink | null {
  if (typeof rawUrl !== 'string') return null;

  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    return null;
  }

  if (url.protocol !== `${DEEP_LINK_SCHEME}:`) return null;

  // `pipeshub://auth/google/callback` parses as host 'auth' + path
  // '/google/callback', so fold the authority back in before matching.
  const segments = [url.host, ...url.pathname.split('/')].filter(Boolean);
  if (segments.length !== 3) return null;
  if (segments[0] !== 'auth' || segments[2] !== 'callback') return null;

  const provider = segments[1];
  if (!isSupportedProvider(provider)) return null;

  const params: Record<string, string> = {};
  url.searchParams.forEach((value, key) => {
    params[key] = value;
  });
  const fragment = url.hash.startsWith('#') ? url.hash.slice(1) : url.hash;
  new URLSearchParams(fragment).forEach((value, key) => {
    params[key] = value;
  });

  return { provider, params };
}

/**
 * Windows and Linux deliver the link as a process argument rather than an
 * event, both on cold start and through the second-instance hook.
 */
export function findDeepLinkInArgv(argv: readonly string[]): string | null {
  if (!Array.isArray(argv)) return null;
  for (const arg of argv) {
    if (typeof arg === 'string' && arg.startsWith(`${DEEP_LINK_SCHEME}://`)) return arg;
  }
  return null;
}
