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

# Azure SAS (sig), S3 SigV4 / GCS SigV2 (x-amz-signature / signature), GCS V4.
# Kept in step with SIGNED_URL_QUERY_PARAMS in the Node enterprise_search signed-url util.
SIGNATURE_QUERY_PARAMS = frozenset({"sig", "signature", "x-amz-signature", "x-goog-signature"})


def has_signature_query(url: str) -> bool:
    """True when the URL's query or fragment carries a storage-provider signature."""
    if not url:
        return False
    parts = re.split(r"[?#]", url)[1:]
    return any(
        key.lower() in SIGNATURE_QUERY_PARAMS
        for part in parts
        for key, _ in parse_qsl(part, keep_blank_values=True)
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
