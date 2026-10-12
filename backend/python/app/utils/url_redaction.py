"""Keep credentials in URLs out of logs and error messages."""

import re
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

# Kept in step with backend/nodejs/apps/src/libs/utils/log-redaction.utils.ts.
# `state` is listed because the connector callback accepts exactly the
# (code, state) pair; `key` only ever as a whole parameter name (`?key=`).
SENSITIVE_QUERY_PARAMS = frozenset({
    "code",
    "state",
    "key",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "client_secret",
    "api_key",
    "apikey",
    "password",
    "signature",
    "sig",
    "x-amz-signature",
    "x-amz-credential",
    "x-amz-security-token",
    "se",
    "sp",
})

# Matched against whole words of a name, never substrings, so `passage` or
# `keyword` stay readable while `X-Amz-Signature` or `apiKey` do not.
CREDENTIAL_WORDS = frozenset({
    "password",
    "passwd",
    "pass",
    "pwd",
    "secret",
    "token",
    "signature",
    "sig",
    "credential",
    "credentials",
    "apikey",
})

REDACTED = "[REDACTED]"


# How many layers of URL-inside-a-parameter (or percent-encoding) are opened.
_MAX_NESTING = 3


def _decode_name(key: str) -> str:
    """Parsers split on a raw ``&`` or ``=`` before decoding, so a pair sent as
    ``code%3Dvalue`` arrives as the single name "code=value", and one sent as
    ``code%253Dvalue`` is still ``code%3Dvalue`` after the parser's decode."""
    name = key
    for _ in range(_MAX_NESTING):
        if "%" not in name:
            break
        decoded = unquote(name)
        if decoded == name:
            break
        name = decoded
    return name


def _param_name(key: str) -> str:
    return _decode_name(key).split("=", 1)[0]


def is_sensitive_query_key(key: str) -> bool:
    name = _param_name(key)
    if name.lower() in SENSITIVE_QUERY_PARAMS:
        return True
    words = [w for w in re.split(r"[^a-z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).lower()) if w]
    return any(
        word in CREDENTIAL_WORDS or (word == "api" and words[i + 1 : i + 2] == ["key"])
        for i, word in enumerate(words)
    )


def redact_url(url: str) -> str:
    """Scheme + host[:port] + path only: userinfo, query and fragment can carry tokens."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return "<unparseable-url>"
    if not parsed.scheme or not parsed.netloc:
        return "<redacted-url>"
    # Drop userinfo (`user:pass@`) while keeping host/port, including bracketed IPv6.
    netloc = parsed.netloc.rsplit("@", 1)[-1]
    return urlunparse((parsed.scheme, netloc, parsed.path or "", "", "", ""))


# Encoded `=`, `?`, `#`, `&`, `@`, `://`, or a further layer of encoding (`%25`).
_ENCODED_URL_SYNTAX = re.compile(r"%(3d|3f|23|26|40|25)|%3a%2f%2f", re.IGNORECASE)

# scheme://user:password@host: the authority runs to the first `/`, `?` or
# `#`, and its last `@` ends the userinfo, so a password holding a raw `@`
# is still caught.
_USERINFO = re.compile(r"^([a-z][a-z0-9+.-]*://)([^/?#]*)@", re.IGNORECASE)


def _redact_userinfo(url: str) -> str:
    """Replace the password of a ``scheme://user:password@host`` URL, for any
    scheme, keeping the user name and host readable as the Node helper does."""
    match = _USERINFO.match(url)
    if not match or ":" not in match.group(2):
        return url
    user = match.group(2).split(":", 1)[0]
    return f"{match.group(1)}{user}:REDACTED@{url[match.end():]}"


def _redact_name(key: str, depth: int) -> str:
    """A name that decodes to more than one pair (``page%3D1%26code%3D<code>``
    is the single name "page=1&code=<code>") is logged with those pairs
    redacted. Returns the name unchanged when nothing in it was secret."""
    name = _decode_name(key)
    if depth > _MAX_NESTING or not re.search(r"[=&]", name):
        return key
    redacted = _redact_pairs(name, depth + 1)
    return redacted if redacted != name else key


def _redact_pairs(segment: str, depth: int = 0) -> str:
    pairs = parse_qsl(segment, keep_blank_values=True)
    changed = False
    out = []
    for key, value in pairs:
        if is_sensitive_query_key(key):
            out.append((_param_name(key), REDACTED))
            changed = True
            continue
        name = _redact_name(key, depth)
        inner = _redact_embedded(value, depth + 1)
        changed = changed or name != key or inner != value
        out.append((name, inner))
    return urlencode(out, safe="[]") if changed else segment


def _redact_embedded(value: str, depth: int) -> str:
    """A parameter value can itself be a URL or query string (``returnTo``,
    ``redirect_uri``), possibly encoded once more than the outer URL."""
    if depth > _MAX_NESTING or not value:
        return value
    if "?" in value or "#" in value:
        return _redact_segments(value, depth)
    # A decoded `scheme://user:password@host` may have no query of its own.
    text = _redact_userinfo(value)
    if "=" in text:
        return _redact_pairs(text, depth)
    if _ENCODED_URL_SYNTAX.search(text):
        decoded = unquote(text)
        if decoded != text:
            inner = _redact_embedded(decoded, depth + 1)
            if inner != decoded:
                return inner
    return text


def _redact_segments(url: str, depth: int) -> str:
    url = _redact_userinfo(url)
    parts = re.split(r"([?#])", url)
    if len(parts) == 1:
        # A whole URL logged percent-encoded hides its `?` as `%3F`.
        return _redact_embedded(url, depth) if depth == 0 else url
    try:
        redacted = [parts[0]] + [
            part if i % 2 == 0 else _redact_pairs(part, depth)
            for i, part in enumerate(parts[1:])
        ]
    except ValueError:
        return parts[0]
    return "".join(redacted)


def redact_sensitive_query_params(url: str) -> str:
    """Replace the values of credential-bearing query params, keeping the path
    and the rest of the query so access logs stay useful.

    Every segment after a raw ``?`` or ``#`` is treated as query pairs: uvicorn's
    h11 protocol splits the request target only at ``?``, so a literal
    ``#token=...`` sent by a client reaches the access log unparsed. URLs
    nested in parameter values are redacted the same way, a bounded number of
    layers deep, as are the password of a ``user:password@`` URL and pairs
    hidden in a percent-encoded parameter name.
    """
    if not url:
        return url
    return _redact_segments(url, 0)
