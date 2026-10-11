/**
 * Redaction for anything request-shaped that ends up in a log line.
 *
 * Access logs, error logs and command logs all record URLs, and any credential
 * a client puts in a query string is written to log storage verbatim. OAuth
 * callbacks are the concrete case here: an authorization code is single-use and
 * short-lived, but it is still exchangeable for tokens by whoever reads it
 * first. The Referer of a request made from a callback page carries the same
 * code, so every URL-valued field is treated alike, wherever it is logged.
 *
 * Kept in step with backend/python/app/utils/url_redaction.py.
 */

/**
 * Query names whose value grants access on its own.
 *
 * `state` is here although it is not a secret by itself: the connector flow
 * packs the connector id and the server-side anti-forgery value into it, and
 * the callback accepts exactly the (code, state) pair, so a logged state is
 * half of a replayable callback. `key` is matched only as a whole query name
 * (Google API keys travel as `?key=`); it is not a credential word below, so
 * `keyword` or `sec-websocket-key` stay readable.
 */
const SENSITIVE_QUERY_PARAMS = new Set([
  'code',
  'state',
  'token',
  'access_token',
  'refresh_token',
  'id_token',
  'client_secret',
  'api_key',
  'apikey',
  'key',
  'password',
  'signature',
  'sig',
  'x-amz-signature',
  'x-amz-credential',
  'x-amz-security-token',
  'se',
  'sp',
]);

// Matched against whole words of a name, never substrings, so `passage` or
// `bypass` stay readable while `X-Amz-Signature` or `apiKey` do not.
const CREDENTIAL_WORDS = new Set([
  'password',
  'passwd',
  'pass',
  'pwd',
  'secret',
  'token',
  'signature',
  'sig',
  'credential',
  'credentials',
  'apikey',
]);

const SECRET_HEADERS = new Set([
  'authorization',
  'proxy-authorization',
  'cookie',
  'set-cookie',
  'x-session-token',
  'x-access-token',
  'x-api-key',
]);

const URL_HEADERS = new Set([
  'referer',
  'referrer',
  'location',
  'content-location',
  'x-original-url',
  'x-forwarded-uri',
  'x-rewrite-url',
]);

const URL_LIKE_KEYS = new Set(['path', 'href']);

export const REDACTED = '[REDACTED]';

const PLACEHOLDER_ORIGIN = 'http://placeholder.invalid';

// How many layers of URL-inside-a-parameter (or percent-encoding) are opened.
const MAX_NESTING = 3;
const MAX_METADATA_DEPTH = 8;

const ENCODED_URL_SYNTAX = /%(3d|3f|23|26)/i;

const keyWords = (key: string): string[] =>
  key
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((word) => word !== '');

// A pair sent as `code%3Dvalue` decodes to the single name "code=value".
const paramName = (key: string): string => key.split('=')[0] ?? key;

/** Whether a query parameter (or header) name carries a credential. */
export const isSensitiveQueryKey = (key: string): boolean => {
  const name = paramName(key);
  if (SENSITIVE_QUERY_PARAMS.has(name.toLowerCase())) return true;
  const words = keyWords(name);
  return words.some(
    (word, i) =>
      CREDENTIAL_WORDS.has(word) || (word === 'api' && words[i + 1] === 'key'),
  );
};

const tryDecode = (value: string): string | null => {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
};

function redactParams(
  params: URLSearchParams,
  depth: number,
): URLSearchParams | null {
  let changed = false;
  const out = new URLSearchParams();
  for (const [key, value] of params) {
    if (isSensitiveQueryKey(key)) {
      out.append(paramName(key), REDACTED);
      changed = true;
      continue;
    }
    const inner = redactEmbedded(value, depth + 1);
    if (inner !== value) changed = true;
    out.append(key, inner);
  }
  return changed ? out : null;
}

/**
 * A parameter value or fragment can itself be a URL or a query string
 * (`returnTo`, `redirect_uri`, an SPA `#/route?code=`), possibly encoded once
 * more than the outer URL. Returns the input unchanged when nothing was found.
 */
function redactEmbedded(value: string, depth: number): string {
  if (depth > MAX_NESTING || value === '') return value;
  if (/[?#]/.test(value)) return redactUrl(value, depth);
  if (value.includes('=')) {
    const redacted = redactParams(new URLSearchParams(value), depth);
    return redacted ? redacted.toString() : value;
  }
  if (ENCODED_URL_SYNTAX.test(value)) {
    const decoded = tryDecode(value);
    if (decoded !== null && decoded !== value) {
      const inner = redactEmbedded(decoded, depth + 1);
      if (inner !== decoded) return inner;
    }
  }
  return value;
}

function redactUrl(url: string, depth: number): string {
  if (!/[?#]/.test(url)) {
    // A whole URL logged percent-encoded hides its `?` as `%3F`.
    return redactEmbedded(url, depth);
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
      return url.split(/[?#]/)[0] ?? '';
    }
  }

  let changed = false;
  const query = redactParams(parsed.searchParams, depth);
  if (query) {
    parsed.search = query.toString();
    changed = true;
  }
  // Implicit-grant OAuth returns its tokens in the fragment.
  const fragment = parsed.hash.slice(1);
  if (fragment !== '') {
    const redacted = redactEmbedded(fragment, depth + 1);
    if (redacted !== fragment) {
      parsed.hash = redacted;
      changed = true;
    }
  }
  if (isAbsolute && parsed.password !== '') {
    parsed.password = 'REDACTED';
    changed = true;
  }
  if (!changed) {
    return url;
  }
  if (isAbsolute) {
    return parsed.toString();
  }
  return `${url.split(/[?#]/)[0] ?? ''}${parsed.search}${parsed.hash}`;
}

/**
 * Return `url` with the values of sensitive query parameters replaced.
 *
 * Takes both shapes logs carry: request lines arrive relative ("/cb?code=..."),
 * while a Referer or Location is absolute. An absolute input keeps its origin,
 * since dropping it would strip the very thing that makes a referrer worth
 * logging; a relative one keeps its path as written.
 *
 * The fragment, URLs nested inside parameters, and further layers of
 * percent-encoding are redacted the same way. The path is left intact so logs
 * stay useful for routing and latency work. A URL that fails to parse loses
 * its query and fragment rather than being passed through: an unparseable
 * value is exactly where a secret could survive a naive regex.
 */
export function redactSensitiveQueryParams(url: string): string {
  if (typeof url !== 'string' || url === '') {
    return url;
  }
  return redactUrl(url, 0);
}

const redactUrlValue = (value: unknown): unknown => {
  if (typeof value === 'string') return redactSensitiveQueryParams(value);
  if (Array.isArray(value)) {
    const out = value.map(redactUrlValue);
    return out.some((item, i) => item !== value[i]) ? out : value;
  }
  return value;
};

/**
 * Headers fit to log: credentials replaced by a marker (so a reader still sees
 * that one was sent), URL-valued headers redacted.
 */
export function redactHeadersForLog(
  headers: Record<string, unknown> | undefined | null,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [name, value] of Object.entries(headers ?? {})) {
    const lower = name.toLowerCase();
    if (SECRET_HEADERS.has(lower) || isSensitiveQueryKey(lower)) {
      out[name] = REDACTED;
    } else if (URL_HEADERS.has(lower)) {
      out[name] = redactUrlValue(value);
    } else {
      out[name] = value;
    }
  }
  return out;
}

/** A parsed query object (Express `req.query`) fit to log. */
export function redactQueryForLog(query: unknown, depth = 0): unknown {
  if (depth > MAX_NESTING) return REDACTED;
  if (typeof query === 'string') return redactEmbedded(query, 1);
  if (Array.isArray(query)) {
    return query.map((item) => redactQueryForLog(item, depth + 1));
  }
  if (query === null || typeof query !== 'object') return query;
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(query)) {
    out[key] = isSensitiveQueryKey(key)
      ? REDACTED
      : redactQueryForLog(value, depth + 1);
  }
  return out;
}

const isUrlLikeKey = (key: string): boolean => {
  const lower = key.toLowerCase();
  return (
    URL_LIKE_KEYS.has(lower) ||
    URL_HEADERS.has(lower) ||
    /ur[li]s?$/.test(lower)
  );
};

const isPlainContainer = (value: unknown): value is object => {
  if (value === null || typeof value !== 'object') return false;
  if (Array.isArray(value)) return true;
  const proto: unknown = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
};

/**
 * Safety net applied to every log line's metadata, so a call site that logs a
 * request field directly cannot leak it: URL-valued fields (`url`,
 * `originalUrl`, `redirectUrl`, `path`, `referer`, `location`, ...) are
 * redacted and secret-bearing header fields are replaced, at any depth of
 * plain objects and arrays. Copy-on-write: metadata with nothing to redact is
 * returned as the same object, and class instances such as Errors are left
 * untouched.
 */
export function redactLogMetadata(
  meta: unknown,
  depth = 0,
  seen: WeakSet<object> = new WeakSet(),
): unknown {
  if (depth > MAX_METADATA_DEPTH || !isPlainContainer(meta) || seen.has(meta)) {
    return meta;
  }
  seen.add(meta);

  if (Array.isArray(meta)) {
    const out = meta.map((item) => redactLogMetadata(item, depth + 1, seen));
    return out.some((item, i) => item !== meta[i]) ? out : meta;
  }

  let out: Record<string, unknown> | null = null;
  for (const [key, value] of Object.entries(meta)) {
    let next: unknown;
    if (SECRET_HEADERS.has(key.toLowerCase()) && value != null) {
      next = REDACTED;
    } else if (
      isUrlLikeKey(key) &&
      (typeof value === 'string' || Array.isArray(value))
    ) {
      next = redactLogMetadata(redactUrlValue(value), depth + 1, seen);
    } else {
      next = redactLogMetadata(value, depth + 1, seen);
    }
    if (next !== value) {
      out ??= { ...(meta as Record<string, unknown>) };
      out[key] = next;
    }
  }
  return out ?? meta;
}
