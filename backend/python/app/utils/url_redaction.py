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


def is_sensitive_query_key(key: str) -> bool:
    # A pair sent as `code%3Dvalue` decodes to the single name "code=value".
    name = key.split("=", 1)[0]
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


# How many layers of URL-inside-a-parameter (or percent-encoding) are opened.
_MAX_NESTING = 3
_ENCODED_URL_SYNTAX = re.compile(r"%(3d|3f|23|26)", re.IGNORECASE)


def _redact_pairs(segment: str, depth: int = 0) -> str:
    pairs = parse_qsl(segment, keep_blank_values=True)
    changed = False
    out = []
    for key, value in pairs:
        if is_sensitive_query_key(key):
            out.append((key.split("=", 1)[0], REDACTED))
            changed = True
            continue
        inner = _redact_embedded(value, depth + 1)
        changed = changed or inner != value
        out.append((key, inner))
    return urlencode(out, safe="[]") if changed else segment


def _redact_embedded(value: str, depth: int) -> str:
    """A parameter value can itself be a URL or query string (``returnTo``,
    ``redirect_uri``), possibly encoded once more than the outer URL."""
    if depth > _MAX_NESTING or not value:
        return value
    if "?" in value or "#" in value:
        return _redact_segments(value, depth)
    if "=" in value:
        return _redact_pairs(value, depth)
    if _ENCODED_URL_SYNTAX.search(value):
        decoded = unquote(value)
        if decoded != value:
            inner = _redact_embedded(decoded, depth + 1)
            if inner != decoded:
                return inner
    return value


def _redact_segments(url: str, depth: int) -> str:
    parts = re.split(r"([?#])", url)
    if len(parts) == 1:
        return url
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
    layers deep.
    """
    if not url:
        return url
    return _redact_segments(url, 0)
