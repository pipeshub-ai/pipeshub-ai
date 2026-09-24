/**
 * Query-string redaction for request logs.
 *
 * Access logs record the full request URL, so any credential a client puts in
 * the query string is written to log storage verbatim. OAuth callbacks are the
 * concrete case here: an authorization code is single-use and short-lived, but
 * it is still exchangeable for tokens by whoever reads it first.
 */

const SENSITIVE_QUERY_PARAMS = new Set([
  'code',
  'token',
  'access_token',
  'refresh_token',
  'id_token',
  'client_secret',
  'api_key',
  'apikey',
  'password',
  'signature',
  'sig',
  'x-amz-signature',
  'x-amz-credential',
  'x-amz-security-token',
  'se',
  'sp',
]);

export const REDACTED = '[REDACTED]';

const PLACEHOLDER_ORIGIN = 'http://placeholder.invalid';

/**
 * Return `url` with the values of sensitive query parameters replaced.
 *
 * Handles both shapes the access log carries: request lines arrive relative
 * ("/cb?code=..."), while a Referer is absolute. An absolute input keeps its
 * origin, since dropping it would strip the very thing that makes a referrer
 * worth logging; a relative one is rebuilt as path + query.
 *
 * The path is left intact so logs stay useful for routing and latency work;
 * only the values that authorize something are removed. A URL that fails to
 * parse is reduced to its path rather than passed through — an unparseable
 * value is exactly the case where a secret could survive a naive regex.
 */
export function redactSensitiveQueryParams(url: string): string {
  if (!url || !url.includes('?')) {
    return url;
  }
  let parsed: URL;
  let isAbsolute = true;
  try {
    parsed = new URL(url);
  } catch {
    try {
      parsed = new URL(url, PLACEHOLDER_ORIGIN);
      isAbsolute = false;
    } catch {
      return url.split('?')[0] ?? url;
    }
  }

  let changed = false;
  for (const key of [...parsed.searchParams.keys()]) {
    if (SENSITIVE_QUERY_PARAMS.has(key.toLowerCase())) {
      parsed.searchParams.set(key, REDACTED);
      changed = true;
    }
  }
  if (!changed) {
    return url;
  }
  return isAbsolute ? parsed.toString() : `${parsed.pathname}${parsed.search}`;
}
