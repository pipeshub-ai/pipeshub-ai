"""Keep credentials in URLs out of logs and error messages."""

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


def redact_sensitive_query_params(url: str) -> str:
    """Replace the values of credential-bearing query params, keeping the path
    and the rest of the query so access logs stay useful."""
    if not url or "?" not in url:
        return url
    try:
        parsed = urlparse(url)
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
    except ValueError:
        return url.split("?", 1)[0]
    if not any(key.lower() in SENSITIVE_QUERY_PARAMS for key, _ in pairs):
        return url
    query = urlencode(
        [(k, REDACTED if k.lower() in SENSITIVE_QUERY_PARAMS else v) for k, v in pairs],
        safe="[]",
    )
    return urlunparse(parsed._replace(query=query))
