import { getFrontendOrigin } from '@/lib/auth/desktop-oauth';
import { apiClient } from './axios-instance';

const FRONTEND_PUBLIC_URL_ENDPOINT = '/api/v1/configurationManager/frontendPublicUrl';

// The desktop renderer's origin is `app://.`, which no OAuth provider accepts, so the
// browser origin is only a usable fallback when it is http(s).
function httpOriginOrNull(): string | null {
  if (typeof window === 'undefined') return null;
  const { origin, protocol } = window.location;
  return protocol === 'http:' || protocol === 'https:' ? origin : null;
}

/**
 * `baseUrl` hint for OAuth requests. The backend only trusts it when it matches the configured
 * frontend URL, so on desktop send nothing and let the backend use its configured value.
 */
export function browserOAuthBaseUrl(): string {
  return httpOriginOrNull() ?? '';
}

// On desktop, the value the sign-in screen learned from the unauthenticated endpoint.
function fallbackBaseUrl(): string | null {
  return httpOriginOrNull() ?? getFrontendOrigin();
}

let inFlight: Promise<string | null> | null = null;

/**
 * The configured public frontend URL — the base the backend builds OAuth redirect URIs
 * from (`app/utils/oauth_redirect.py`). Concurrent callers share one request; each new
 * call re-reads because admins can change the value.
 */
export function fetchFrontendPublicUrl(): Promise<string | null> {
  if (inFlight) return inFlight;
  inFlight = apiClient
    .get<{ url?: string }>(FRONTEND_PUBLIC_URL_ENDPOINT)
    .then(({ data }) => data?.url?.trim() || fallbackBaseUrl())
    .catch(() => fallbackBaseUrl())
    .then((url) => (url ? url.replace(/\/+$/, '') : null))
    .finally(() => {
      inFlight = null;
    });
  return inFlight;
}

export function joinRedirectUri(baseUrl: string, path: string): string {
  return `${baseUrl.replace(/\/+$/, '')}/${path.replace(/^\/+/, '')}`;
}
