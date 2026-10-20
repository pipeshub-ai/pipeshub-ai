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

// The longest URL or query text redacted for a log line; the rest is dropped.
// Every pass below is linear, but there are many of them, and Node accepts a
// request line or Referer of up to 16 KiB from anyone, before authentication.
const MAX_LOGGED_TEXT_LENGTH = 8 * 1024;

// Encoded `=`, `?`, `#`, `&`, `@`, `://`, or a further layer of encoding (`%25`).
const ENCODED_URL_SYNTAX = /%(3d|3f|23|26|40|25)|%3a%2f%2f/i;

const keyWords = (key: string): string[] =>
  key
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((word) => word !== '');

// Decode a run of `%XX` escapes as UTF-8 bytes. A byte that is not valid
// UTF-8 becomes U+FFFD, as Python's `unquote` does, rather than leaving the
// whole run encoded: `code%FF%3D<code>` must still show its `=`.
const decodeRun = (run: string): string =>
  Buffer.from(run.replace(/%/g, ''), 'hex').toString('utf8');

// Decode each run of escapes on its own, so one malformed `%` elsewhere in
// the text cannot stop the rest from being read.
const decodeEscapes = (text: string): string =>
  text.replace(/(?:%[0-9a-f]{2})+/gi, decodeRun);

// One layer of decoding that never makes a new `&` or `#`. Once a text has to
// be decoded to show its `?` and `=`, an `%26` or `%23` at that same layer may
// sit inside a value (`code%3D4%2F0Aea%26TAIL` is the code `4/0Aea&TAIL`), so
// it stays encoded and the parse below reads it as part of that value.
const decodeSyntax = (text: string): string =>
  text.replace(/(?:%[0-9a-f]{2})+/gi, (run) =>
    decodeRun(run).replace(/[&#]/g, (ch) => (ch === '&' ? '%26' : '%23')),
  );

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

/**
 * The name a sensitive parameter is logged under: only its leading word
 * characters, so whatever a decode left after them (`X-Amz-Signature%3D<sig>`
 * or `code\u0000`) is never written out with it.
 */
const loggedName = (key: string): string =>
  /^[\w.-]+/.exec(paramName(key))?.[0] ?? REDACTED;

// ASCII only, as /[^a-z0-9]/i is without the `u` flag: `ſ` and the Kelvin
// sign `K` are not letters here.
const isAsciiAlphanumeric = (code: number): boolean =>
  (code >= 48 && code <= 57) || ((code | 32) >= 97 && (code | 32) <= 122);

// An index scan rather than /^[^a-z0-9]+|[^a-z0-9]+$/: the end-anchored
// alternative is retried from every offset of a long non-alphanumeric run.
const trimNonAlphanumeric = (text: string): string => {
  let start = 0;
  let end = text.length;
  while (start < end && !isAsciiAlphanumeric(text.charCodeAt(start)))
    start += 1;
  while (end > start && !isAsciiAlphanumeric(text.charCodeAt(end - 1)))
    end -= 1;
  return text.slice(start, end);
};

/**
 * Whether a query parameter (or header) name carries a credential. Stray
 * non-alphanumeric bytes around the name (`code%00`, `code.`, `code `) do not
 * hide it.
 */
export const isSensitiveQueryKey = (key: string): boolean => {
  const name = trimNonAlphanumeric(paramName(key));
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
  // itself parsed as a URL or query. Every `&` here was decoded, though, so
  // one may have been inside the secret just redacted: nothing after a
  // redacted pair is kept.
  const pairs: string[] = [];
  for (const pair of name.split('&')) {
    const eq = pair.indexOf('=');
    const pairKey = eq === -1 ? pair : pair.slice(0, eq);
    if (isSensitiveQueryKey(pairKey)) {
      pairs.push(`${loggedName(pairKey)}=${REDACTED}`);
      return pairs.join('&');
    }
    const keyOut = redactParamName(pairKey, depth + 1);
    const value = eq === -1 ? null : pair.slice(eq + 1);
    const valueOut = value === null ? null : redactEmbedded(value, depth + 1);
    if (keyOut === pairKey && valueOut === value) {
      pairs.push(pair);
      continue;
    }
    pairs.push(valueOut === null ? keyOut : `${keyOut}=${valueOut}`);
    return pairs.join('&');
  }
  return key;
}

/**
 * `trusted` says whether the `&` separators of the text these pairs came from
 * were written as raw `&` in the logged URL. In text that had to be decoded
 * first, an `&` may have been an `%26` inside the secret just redacted, so
 * nothing after a redacted pair is kept.
 */
function redactParams(
  params: URLSearchParams,
  depth: number,
  trusted: boolean,
): URLSearchParams | null {
  let changed = false;
  const out = new URLSearchParams();
  for (const [key, value] of params) {
    if (isSensitiveQueryKey(key)) {
      out.append(loggedName(key), REDACTED);
    } else {
      const name = redactParamName(key, depth);
      const inner = redactEmbedded(value, depth + 1);
      out.append(name, inner);
      if (name === key && inner === value) continue;
    }
    changed = true;
    if (!trusted) break;
  }
  return changed ? out : null;
}

/**
 * A parameter value or fragment can itself be a URL or a query string
 * (`returnTo`, `redirect_uri`, an SPA `#/route?code=`), possibly encoded once
 * more than the outer URL. Returns the input unchanged when nothing was found.
 */
function redactEmbedded(value: string, depth: number, trusted = false): string {
  if (depth > MAX_NESTING || value === '') return value;
  if (/[?#]/.test(value)) return redactUrl(value, depth, trusted);
  // A decoded `scheme://user:password@host` may have no query of its own.
  const text = redactUserinfo(value);
  if (text !== value && /[?#]/.test(text)) {
    return redactUrl(text, depth, trusted);
  }
  if (text.includes('=')) {
    const redacted = redactParams(new URLSearchParams(text), depth, trusted);
    return redacted ? redacted.toString() : text;
  }
  if (ENCODED_URL_SYNTAX.test(text)) {
    // Run by run, so a stray `%` cannot leave the whole text undecoded. No
    // new `&` or `#` is made, so the separators keep the trust they had.
    const decoded = decodeSyntax(text);
    if (decoded !== text) {
      const inner = redactEmbedded(decoded, depth + 1, trusted);
      if (inner !== decoded) return inner;
    }
  }
  return text;
}

const SCHEME = /^([a-z][a-z0-9+.-]*:\/\/)/i;
// Where a URL that can carry `user:password@` starts: `scheme://`, a
// protocol-relative `//`, or `scheme:` written without the `//`.
const USERINFO_START = /^(?:[a-z][a-z0-9+.-]*:)?\/\/|^[a-z][a-z0-9+.-]*:/i;
const USERINFO_COLON = /:|%(?:25)*3a/i;
// A user name never holds the end of an authority, raw or encoded.
// A user name may hold an `@` (`alice@example.com:pw@host`) or whitespace
// (`John Doe:pw@host`).
const NOT_A_USER = /[/?#[\]]|%(?:25)*(?:2f|3f|23)/i;
// `host:8080/…` is a port, not a password: one or more digits, then a raw
// `/`, `?`, `#`, `,` (several hosts), whitespace or the end, and no `@` raw
// or encoded before the query, fragment or end. Past that `?` or `#`, each
// `&`, `;`, `?` or `#` segment that holds an `@` must have an `=` before it:
// in a query an `@` sits inside a pair (`?page=1&email=a@b.com`), while a
// userinfo `@` does not (`app:1234?page=1&secret@host`).
const QUERY_SEGMENT = String.raw`(?:(?!%(?:25)*40)[^@=&;?#])*(?:=[^&;?#]*)?`;
const PORT = new RegExp(
  String.raw`^\d+(?=[/?#,\s]|$)(?:(?!%(?:25)*40)[^?#@])*` +
    String.raw`(?:$|[?#]${QUERY_SEGMENT}(?:[&;?#]${QUERY_SEGMENT})*$)`,
  'i',
);
const ANY_AT = /@|%(?:25)*40/gi;
// Query or fragment syntax, raw or encoded, inside what is read as a password.
const QUERY_MARK = /[?#;&]|%(?:25)*(?:3f|23|3b|26)/i;

interface PasswordUrl {
  start: string; // `scheme://`, `//` or `scheme:`
  user: string;
  rest: string; // what follows the password, as written: `@host…` or `?…`
}

/**
 * Split a URL that carries a password into the text before it, the user
 * name, and what follows it, or return null when there is none.
 *
 * Kept deliberately simple, since every finer rule has missed an encoding:
 * - The user name runs to the first `:` and holds no `/`, `?` or `#`; it may
 *   hold an `@` or whitespace.
 * - If a port follows that `:` (see `PORT`), it is not a password.
 * - Otherwise everything up to the last `@` in the rest of the text is the
 *   password, whatever it holds (`/`, `?`, `#`, spaces, a further `@`, raw
 *   or encoded). Nothing is decoded, so the text after that `@` stays
 *   exactly as written.
 * - The host after it is kept only when that `@` is the only one and no
 *   query or fragment syntax comes before it. Otherwise either reading could
 *   put part of a secret there, so the host is replaced too.
 * - A `//` URL with no `@` at all (`https://user:pw?x=1`) has the text after
 *   the `:` replaced up to its query.
 *
 * This over-reads `http://h:abc/x` or `https://u:p@h/a@b` and loses some of
 * them to the redaction, which costs readability and never a secret.
 */
function splitPasswordUrl(url: string): PasswordUrl | null {
  const start = USERINFO_START.exec(url)?.[0];
  if (start === undefined) return null;
  const after = url.slice(start.length);
  const colon = USERINFO_COLON.exec(after);
  if (!colon) return null;
  const user = after.slice(0, colon.index);
  if (NOT_A_USER.test(user)) return null;
  const rest = after.slice(colon.index + colon[0].length);
  if (PORT.test(rest)) return null;
  const ats = [...rest.matchAll(ANY_AT)];
  const last = ats[ats.length - 1];
  if (last === undefined) {
    if (!start.endsWith('//')) return null;
    const query = rest.search(/[?#]|%(?:25)*(?:3f|23)/i);
    return { start, user, rest: query === -1 ? '' : rest.slice(query) };
  }
  const password = rest.slice(0, last.index);
  const settled = ats.length === 1 && !QUERY_MARK.test(password);
  return {
    start,
    user,
    rest: `@${settled ? rest.slice(last.index + last[0].length) : REDACTED}`,
  };
}

/**
 * Replace the password of a `scheme://user:password@host` URL, for any
 * scheme. Done on the text rather than through `URL`, which rejects
 * multi-host connection strings such as `mongodb://u:p@h1:27017,h2:27017/db`,
 * and reads a `/` in a password as the start of the path.
 */
function redactUserinfo(url: string): string {
  const parts = splitPasswordUrl(url);
  return parts ? `${parts.start}${parts.user}:REDACTED${parts.rest}` : url;
}

function redactUrl(rawUrl: string, depth: number, trusted: boolean): string {
  const url = redactUserinfo(rawUrl);
  if (!/[?#]/.test(url)) {
    // A whole URL logged percent-encoded hides its `?` as `%3F`.
    return redactEmbedded(url, depth, trusted);
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
  const query = redactParams(parsed.searchParams, depth, trusted);
  if (query) {
    parsed.search = query.toString();
    changed = true;
  }
  // Implicit-grant OAuth returns its tokens in the fragment.
  const fragment = parsed.hash.slice(1);
  if (query && !trusted) {
    // A decoded `#` may have been inside the secret just redacted.
    parsed.hash = '';
  } else if (fragment !== '') {
    const redacted = redactEmbedded(fragment, depth + 1, trusted);
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
  return redactCapped(url, (text) => {
    // The logged text itself: its raw `&` and `#` are real separators.
    const redacted = redactUrl(text, 0, true);
    return hasResidualSecret(redacted) ? redactWholly(text) : redacted;
  });
}

/**
 * Free text such as an error message or stack: each word that could hold a
 * `name=value` pair or a `user:password@` URL is redacted on its own, so the
 * rest of the text (stack frames, Mongo key errors) stays readable.
 */
export const redactSecretsInText = (text: string): string =>
  // Split, not a \S*[=@]\S* scan: that pattern backtracks quadratically on a long word.
  text
    .split(/(\s+)/)
    .map((word) =>
      word.includes('=') || word.includes('@')
        ? redactSensitiveQueryParams(word)
        : word,
    )
    .join('')
    .replace(/\b(Bearer|Basic)\s+\S+/gi, `$1 ${REDACTED}`);

// Where a text cut for the log may end: after a raw separator, so that no
// name or value is kept in part.
const CUT_AFTER = new Set(['/', '?', '#', '&', ';', '=', ':', '@']);

/**
 * `redact` applied to at most MAX_LOGGED_TEXT_LENGTH characters of `text`,
 * marked when the rest was dropped. The cut is moved back to a separator,
 * since it could fall inside a secret. A name always comes before its value,
 * so what is kept reads as it did in the whole text, with one exception: a
 * password runs to the last `@`, so once a dropped `@` could end one, no
 * reading of the kept text can be trusted and none of it is logged.
 */
function redactCapped(text: string, redact: (kept: string) => string): string {
  if (text.length <= MAX_LOGGED_TEXT_LENGTH) return redact(text);
  let end = MAX_LOGGED_TEXT_LENGTH;
  while (end > 0 && !CUT_AFTER.has(text.charAt(end - 1))) end -= 1;
  if (text.slice(end).search(ANY_AT) !== -1) return `${REDACTED}${TRUNCATED}`;
  return `${redact(text.slice(0, end))}${TRUNCATED}`;
}

// The safety net below the structural pass. However a URL was encoded, a
// secret only matters if some decoding of the logged text shows it as a
// sensitive `name=value` or a `user:password@` URL. So the output is decoded
// layer by layer, and if any layer still shows one, the text is replaced
// wholesale instead of trusting the parse that missed it.
const MAX_SCAN_LAYERS = 10;
// The pieces `showsPasswordUrl` reads a head and a port with, as written in
// `PORT` and in the regex that function replaces.
const NOT_SCHEME_CHAR = /[^a-z0-9+.-]/i;
const SCHEME_CHAR = /[a-z0-9+.-]/i;
const SCHEME_START = /[a-z]/i;
const AUTHORITY_END = new Set(['/', '?', '#', ':', '[', ']']);
const REDACTED_AT = /REDACTED@/iy;
const PORT_DIGITS = /\d+(?=[/?#,\s]|$)/y;
const PORT_END = /[?#@]|%(?:25)*40/gi;
const QUERY_SEPARATOR = /[&;?#]/g;
const WHOLE_QUERY_SEGMENT = new RegExp(`^${QUERY_SEGMENT}$`, 'i');

/**
 * The shapes `splitPasswordUrl` reads, anywhere in the text: a user name
 * (which may hold an `@` or whitespace) after `//`, or a `scheme:user:` with
 * no `//`, then a `:` followed by neither a port nor `REDACTED@`, and an `@`
 * somewhere after it, past any spaces. A `user:REDACTED` with an `@` still to
 * come is caught too: the redaction stopped short of the real end of the
 * password.
 *
 * This is the regex
 *   (?:^|[^a-z0-9+.-])(?:[a-z][a-z0-9+.-]*:)?//[^/?#:[\]]*:
 *   | (?:^|[^a-z0-9+.-])[a-z][a-z0-9+.-]*:[^/?#:[\]]+:
 * each followed by (?!REDACTED@)(?!PORT)[^@]*@, read in one pass. As a regex,
 * every head re-ran the port check to the end of the text, which took seconds
 * on 16 KiB of `,//a:1`. Here the port check reuses what it found: the first
 * `?`, `#` or `@` after a head, and the last query segment no port accepts.
 */
function showsPasswordUrl(text: string): boolean {
  const lastAt = text.lastIndexOf('@');
  if (lastAt === -1) return false;

  const separators = [...text.matchAll(QUERY_SEPARATOR)].map((m) => m.index);
  let lastBadSegment = -1;
  separators.forEach((at, k) => {
    const segment = text.slice(at + 1, separators[k + 1] ?? text.length);
    if (!WHOLE_QUERY_SEGMENT.test(segment)) lastBadSegment = at;
  });
  let portEnd = -1;
  const isPort = (at: number): boolean => {
    PORT_DIGITS.lastIndex = at;
    if (!PORT_DIGITS.test(text)) return false;
    if (portEnd < at) {
      PORT_END.lastIndex = at;
      portEnd = PORT_END.exec(text)?.index ?? text.length;
    }
    const mark = text.charAt(portEnd);
    return (
      portEnd === text.length ||
      ((mark === '?' || mark === '#') && portEnd > lastBadSegment)
    );
  };
  const isRedactedAt = (at: number): boolean => {
    REDACTED_AT.lastIndex = at;
    return REDACTED_AT.test(text);
  };

  // The last of `/?#:[]` seen, and whether a head may end at the next `:`
  // with what follows it as the user name: `//` there, or a `scheme:`.
  let authorityEnd = -1;
  let afterSlashes = false;
  let afterScheme = false;
  let schemeRun = -1;
  for (let i = 0; i < lastAt; i += 1) {
    const ch = text.charAt(i);
    if (
      ch === ':' &&
      (afterSlashes || (afterScheme && authorityEnd < i - 1)) &&
      !isRedactedAt(i + 1) &&
      !isPort(i + 1)
    ) {
      return true;
    }
    if (AUTHORITY_END.has(ch)) {
      afterSlashes =
        ch === '/' &&
        text.charAt(i - 1) === '/' &&
        (i === 1 || (i > 1 && NOT_SCHEME_CHAR.test(text.charAt(i - 2))));
      afterScheme =
        ch === ':' &&
        schemeRun !== -1 &&
        SCHEME_START.test(text.charAt(schemeRun));
      authorityEnd = i;
    }
    if (!SCHEME_CHAR.test(ch)) schemeRun = -1;
    else if (schemeRun === -1) schemeRun = i;
  }
  return false;
}

// Parsers add a trailing `=` to a bare name, so `[REDACTED]=` is still clean.
const isRedactedValue = (value: string): boolean => {
  // A backward scan, not /=+$/: that is retried from every `=` of a long run.
  let end = value.length;
  while (end > 0 && value.charCodeAt(end - 1) === 61) end -= 1;
  const bare = value.slice(0, end);
  return (
    bare === '' ||
    bare === REDACTED ||
    bare === 'REDACTED' ||
    /^%5BREDACTED%5D$/i.test(bare)
  );
};

// Every `name=` in a segment counts, not just the first: in
// `returnTo=code=<code>` or `code=[REDACTED]=<code>` the secret follows a
// later `=`. A value runs to the next `?`, `&`, `#` or `;`; whitespace does
// not end it, so `code =<code>` is still one pair. A name that ends in a
// redaction marker also counts: the marker may have replaced the sensitive
// name itself (`//u:p@[REDACTED]=<code>`, where the host held `…?code`).
const ENDS_IN_MARKER = /(?:^|[^a-z0-9])\[?REDACTED\]?$/;
// The same, when the value starts with a separator (`[REDACTED]=?<code>`): a
// marker followed by `=` and anything but an empty value or another marker.
const MARKER_THEN_VALUE =
  /(?:^|[^a-z0-9])\[?REDACTED\]?=(?!=*(?:$|[&#;])|\[REDACTED\]|REDACTED\b)/;
const isRedactedName = (name: string): boolean =>
  isSensitiveQueryKey(name) || ENDS_IN_MARKER.test(name);

function showsSecretPair(layer: string): boolean {
  if (MARKER_THEN_VALUE.test(layer)) return true;
  for (const segment of layer.split(/[?&#;]/)) {
    const parts = segment.split('=');
    for (let k = 0; k < parts.length - 1; k += 1) {
      const name = parts[k] ?? '';
      if (
        name !== '' &&
        isRedactedName(name) &&
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
    // Every password shape ends at an `@`, so only the text up to the last
    // one is scanned; that keeps long text with none (or none late) cheap.
    const lastAt = layer.lastIndexOf('@');
    if (lastAt !== -1 && showsPasswordUrl(layer.slice(0, lastAt + 1))) {
      return true;
    }
    if (showsSecretPair(layer)) return true;
    const next = decodeEscapes(layer);
    if (next === layer) return false;
    layer = next;
  }
  return false;
}

// Where the kept part of a fallback ends: any query or fragment, an encoded
// character, and every separator the scan above splits pairs on.
const KEPT_TEXT_END = /[?#%;&=\s]/;

/**
 * The fallback when the parse missed a secret: keep the scheme and host of an
 * absolute URL, or the path of a relative one, and replace everything else.
 * If even that kept part shows a secret, nothing is kept.
 */
function redactWholly(url: string): string {
  const scheme = SCHEME.exec(url)?.[1];
  const parts = splitPasswordUrl(url);
  let kept = REDACTED;
  if (parts) {
    // The host is what follows the password, if anything was kept there.
    const host = parts.rest.startsWith('@')
      ? (parts.rest.slice(1).split(/[/?#%;&=\s]/)[0] ?? '')
      : '';
    kept = `${parts.start}${host}/${REDACTED}`;
  } else if (scheme !== undefined) {
    // The authority after any raw `user@`. It ends at the first encoded
    // character, so an encoded `@` further on is never read as a boundary.
    // The port is left out too: when no password could be split off, what
    // follows the `:` may still be the start of one.
    const authority = url.slice(scheme.length).split(/[/?#%;&=\s]/)[0] ?? '';
    const host = authority.split('@').pop() ?? '';
    kept = `${scheme}${/^\[[^\]]*\]|^[^:]*/.exec(host)?.[0] ?? ''}/${REDACTED}`;
  } else if (url.startsWith('/')) {
    kept = `${url.split(KEPT_TEXT_END)[0] ?? ''}?${REDACTED}`;
  }
  return hasResidualSecret(kept) ? REDACTED : kept;
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
    return redactCapped(query, (text) => {
      const redacted = redactEmbedded(text, 1);
      return hasResidualSecret(redacted) ? REDACTED : redacted;
    });
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
      out[loggedName(key)] = REDACTED;
      continue;
    }
    // A name is not cut as a URL is: its value may be the secret of a pair
    // the dropped part of the name ends with, so neither is logged.
    if (key.length > MAX_LOGGED_TEXT_LENGTH) {
      out[TRUNCATED] = REDACTED;
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
