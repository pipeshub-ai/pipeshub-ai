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
const CIRCULAR = '[Circular]';
const TRUNCATED = '[Truncated]';

// Encoded `=`, `?`, `#`, `&`, `@`, `://`, or a further layer of encoding (`%25`).
const ENCODED_URL_SYNTAX = /%(3d|3f|23|26|40|25)|%3a%2f%2f/i;

const keyWords = (key: string): string[] =>
  key
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((word) => word !== '');

const tryDecode = (value: string): string | null => {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
};

// Decode each run of escapes on its own, so one malformed `%` elsewhere in
// the text cannot stop the rest from being read.
const decodeEscapes = (text: string): string =>
  text.replace(/(?:%[0-9a-f]{2})+/gi, (run) => tryDecode(run) ?? run);

// Parsers split on a raw `&` or `=` before decoding, so a pair sent as
// `code%3Dvalue` arrives as the single name "code=value", and one sent as
// `code%253Dvalue` is still `code%3Dvalue` after the parser's decode.
// Decoding stops once a raw `&` or `=` shows, so a `%26` inside a value
// stays part of that value when the name is split into pairs.
const decodeLayers = (text: string, stop?: RegExp): string => {
  let out = text;
  for (
    let i = 0;
    i < MAX_NESTING && out.includes('%') && !(stop?.test(out) ?? false);
    i += 1
  ) {
    const next = decodeEscapes(out);
    if (next === out) break;
    out = next;
  }
  return out;
};

const decodeName = (key: string): string => decodeLayers(key, /[=&]/);

const paramName = (key: string): string => {
  const name = decodeName(key);
  return name.split('=')[0] ?? name;
};

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

/**
 * A name that decodes to more than one pair (`page%3D1%26code%3D<code>` is
 * the single name "page=1&code=<code>") is read as those pairs and logged
 * redacted. Returns the name unchanged when nothing in it was secret.
 */
function redactParamName(key: string, depth: number): string {
  const name = decodeName(key);
  if (depth > MAX_NESTING || !/[=&]/.test(name)) return key;
  // Split on the raw separators only. Each value stays encoded, so a `%26`
  // inside a nested code is still part of that code when the value is
  // itself parsed as a URL or query.
  let changed = false;
  const pairs: string[] = [];
  for (const pair of name.split('&')) {
    const eq = pair.indexOf('=');
    const pairKey = eq === -1 ? pair : pair.slice(0, eq);
    if (isSensitiveQueryKey(pairKey)) {
      changed = true;
      pairs.push(`${paramName(pairKey)}=${REDACTED}`);
      continue;
    }
    const keyOut = redactParamName(pairKey, depth + 1);
    const value = eq === -1 ? null : pair.slice(eq + 1);
    const valueOut = value === null ? null : redactEmbedded(value, depth + 1);
    if (keyOut === pairKey && valueOut === value) {
      pairs.push(pair);
      continue;
    }
    changed = true;
    pairs.push(valueOut === null ? keyOut : `${keyOut}=${valueOut}`);
  }
  return changed ? pairs.join('&') : key;
}

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
    const name = redactParamName(key, depth);
    const inner = redactEmbedded(value, depth + 1);
    if (name !== key || inner !== value) changed = true;
    out.append(name, inner);
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
  // A decoded `scheme://user:password@host` may have no query of its own.
  const text = redactUserinfo(value);
  if (text !== value && /[?#]/.test(text)) return redactUrl(text, depth);
  if (text.includes('=')) {
    const redacted = redactParams(new URLSearchParams(text), depth);
    return redacted ? redacted.toString() : text;
  }
  if (ENCODED_URL_SYNTAX.test(text)) {
    // Run by run, so a stray `%` cannot leave the whole text undecoded.
    const decoded = decodeEscapes(text);
    if (decoded !== text) {
      const inner = redactEmbedded(decoded, depth + 1);
      if (inner !== decoded) return inner;
    }
  }
  return text;
}

// scheme://user:password@host — the authority runs to the first `/`, `?` or
// `#`, and its last `@` ends the userinfo, so a password holding a raw `@`
// is still caught.
const AUTHORITY = /^([a-z][a-z0-9+.-]*:\/\/)([^/?#]*)/i;

/**
 * Replace the password of a `scheme://user:password@host` URL, for any
 * scheme. Done on the text rather than through `URL`, which rejects
 * multi-host connection strings such as `mongodb://u:p@h1:27017,h2:27017/db`.
 * The authority is decoded first, since a logged URL can carry its `@` or
 * `:` as `%40` or `%3A`.
 */
function redactUserinfo(url: string): string {
  const match = AUTHORITY.exec(url);
  if (!match) return url;
  // Decode only until the `@` shows, then only the userinfo until its `:`
  // shows, so an encoded `?` or `&` further on is not turned into syntax.
  const authority = decodeLayers(match[2] ?? '', /@/);
  const at = authority.lastIndexOf('@');
  if (at === -1) return url;
  const userinfo = decodeLayers(authority.slice(0, at), /:/);
  const colon = userinfo.indexOf(':');
  if (colon === -1) return url;
  const scheme = match[1] ?? '';
  return `${scheme}${userinfo.slice(0, colon)}:REDACTED@${authority.slice(at + 1)}${url.slice(match[0].length)}`;
}

function redactUrl(rawUrl: string, depth: number): string {
  const url = redactUserinfo(rawUrl);
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
  const redacted = redactUrl(url, 0);
  return hasResidualSecret(redacted) ? redactWholly(url) : redacted;
}

// The safety net below the structural pass. However a URL was encoded, a
// secret only matters if some decoding of the logged text shows it as a
// sensitive `name=value` or a `user:password@` URL. So the output is decoded
// layer by layer, and if any layer still shows one, the text is replaced
// wholesale instead of trusting the parse that missed it.
const MAX_SCAN_LAYERS = 10;
const PASSWORD_URL =
  /[a-z][a-z0-9+.-]*:\/\/[^\s/?#@]*:(?!REDACTED@)[^\s/?#@]+@/i;

// Parsers add a trailing `=` to a bare name, so `[REDACTED]=` is still clean.
const isRedactedValue = (value: string): boolean => {
  const bare = value.replace(/=+$/, '');
  return (
    bare === '' ||
    bare === REDACTED ||
    bare === 'REDACTED' ||
    /^%5BREDACTED%5D$/i.test(bare)
  );
};

// Every `name=` in a segment counts, not just the first: in
// `returnTo=code=<code>` or `code=[REDACTED]=<code>` the secret follows a
// later `=`. A value runs to the next `?`, `&`, `#` or `;`.
function showsSecretPair(layer: string): boolean {
  for (const segment of layer.split(/[\s?&#;]/)) {
    const parts = segment.split('=');
    for (let k = 0; k < parts.length - 1; k += 1) {
      const name = parts[k] ?? '';
      if (
        name !== '' &&
        isSensitiveQueryKey(name) &&
        !isRedactedValue(parts.slice(k + 1).join('='))
      ) {
        return true;
      }
    }
  }
  return false;
}

function hasResidualSecret(text: string): boolean {
  let layer = text;
  for (let i = 0; i < MAX_SCAN_LAYERS; i += 1) {
    if (PASSWORD_URL.test(layer)) return true;
    if (showsSecretPair(layer)) return true;
    const next = decodeEscapes(layer);
    if (next === layer) return false;
    layer = next;
  }
  return false;
}

/**
 * The fallback when the parse missed a secret: keep the scheme and host of an
 * absolute URL, or the path of a relative one, and replace everything else.
 */
function redactWholly(url: string): string {
  const absolute = AUTHORITY.exec(url);
  if (absolute) {
    const host = (absolute[2] ?? '').split(/@|%(?:25)*40/i).pop() ?? '';
    return `${absolute[1] ?? ''}${host.split('%')[0] ?? ''}/${REDACTED}`;
  }
  if (url.startsWith('/')) return `${url.split(/[?#%]/)[0] ?? ''}?${REDACTED}`;
  return REDACTED;
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
  if (typeof query === 'string') {
    const redacted = redactEmbedded(query, 1);
    return hasResidualSecret(redacted) ? REDACTED : redacted;
  }
  if (Array.isArray(query)) {
    return query.map((item) => redactQueryForLog(item, depth + 1));
  }
  if (query === null || typeof query !== 'object') return query;
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(query)) {
    // The name itself can hold the secret (`code%3D<code>` parses as a
    // parameter named "code=<code>"), so a sensitive one is logged trimmed,
    // and one holding several pairs is logged with those pairs redacted.
    if (isSensitiveQueryKey(key)) {
      out[paramName(key)] = REDACTED;
      continue;
    }
    const name = redactParamName(key, 1);
    const logged = redactQueryForLog(value, depth + 1);
    // The parser may have split a secret across the name and its value.
    const pair = `${name}=${typeof logged === 'string' ? logged : ''}`;
    if (hasResidualSecret(name)) {
      out[REDACTED] = REDACTED;
    } else if (hasResidualSecret(pair)) {
      out[name] = REDACTED;
    } else {
      out[name] = logged;
    }
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
 * untouched. A cycle becomes "[Circular]" and a plain container nested past
 * the depth limit becomes "[Truncated]", since neither can be checked.
 */
export function redactLogMetadata(
  meta: unknown,
  depth = 0,
  seen: WeakSet<object> = new WeakSet(),
): unknown {
  if (!isPlainContainer(meta)) return meta;
  // Past the limit the fields can no longer be checked, and returning the
  // container would hand it to the serializer whole.
  if (depth > MAX_METADATA_DEPTH) return TRUNCATED;
  if (seen.has(meta)) return CIRCULAR;
  // `seen` holds only the current path, so an object shared by two branches
  // (not a cycle) is redacted in both.
  seen.add(meta);
  try {
    return redactContainer(meta, depth, seen);
  } finally {
    seen.delete(meta);
  }
}

function redactContainer(
  meta: object,
  depth: number,
  seen: WeakSet<object>,
): unknown {
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
