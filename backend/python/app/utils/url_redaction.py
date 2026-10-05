"""Keep credentials in URLs out of logs and error messages."""

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Kept in step with SENSITIVE_QUERY_PARAMS in the Node log-redaction utils.
SENSITIVE_QUERY_PARAMS = frozenset({
    "code",
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

REDACTED = "[REDACTED]"


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


def _redact_pairs(segment: str) -> str:
    pairs = parse_qsl(segment, keep_blank_values=True)
    if not any(key.lower() in SENSITIVE_QUERY_PARAMS for key, _ in pairs):
        return segment
    return urlencode(
        [(k, REDACTED if k.lower() in SENSITIVE_QUERY_PARAMS else v) for k, v in pairs],
        safe="[]",
    )


def redact_sensitive_query_params(url: str) -> str:
    """Replace the values of credential-bearing query params, keeping the path
    and the rest of the query so access logs stay useful.

    Every segment after a raw ``?`` or ``#`` is treated as query pairs: uvicorn's
    h11 protocol splits the request target only at ``?``, so a literal
    ``#token=...`` sent by a client reaches the access log unparsed.
    """
    if not url:
        return url
    parts = re.split(r"([?#])", url)
    if len(parts) == 1:
        return url
    try:
        redacted = [parts[0]] + [
            part if i % 2 == 0 else _redact_pairs(part)
            for i, part in enumerate(parts[1:])
        ]
    except ValueError:
        return parts[0]
    return "".join(redacted)
