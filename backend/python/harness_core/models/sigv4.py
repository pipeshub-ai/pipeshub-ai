"""AWS Signature Version 4 for one HTTP request (P4.3, the Bedrock Converse transport).

Standard library only, so ``harness_core`` takes no AWS SDK. Matches
``botocore.auth.SigV4Auth`` for the services that are not S3: the path is
encoded once on the wire and once more in the canonical request, every header
the request carries is signed (``host`` and ``x-amz-date`` included), and the
payload hash is not sent as a header.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import TYPE_CHECKING, override
from urllib.parse import parse_qsl, quote, urlsplit

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

ALGORITHM = "AWS4-HMAC-SHA256"
_UNSIGNED = frozenset({"expect", "user-agent", "x-amzn-trace-id", "authorization"})


@dataclass(frozen=True)
class AwsCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: str | None = None

    @override
    def __repr__(self) -> str:
        return f"AwsCredentials(access_key_id={self.access_key_id!r})"


def _hmac(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, date: str, region: str, service: str) -> bytes:
    key = _hmac(f"AWS4{secret}".encode(), date)
    for part in (region, service, "aws4_request"):
        key = _hmac(key, part)
    return key


def _canonical_query(query: str) -> str:
    pairs = sorted(
        (quote(k, safe="-_.~"), quote(v, safe="-_.~")) for k, v in parse_qsl(query, keep_blank_values=True)
    )
    return "&".join(f"{k}={v}" for k, v in pairs)


def _host(url: str) -> str:
    parts = urlsplit(url)
    default = {"https": 443, "http": 80}.get(parts.scheme)
    host = parts.hostname or ""
    return host if parts.port in (None, default) else f"{host}:{parts.port}"


def sign(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    *,
    credentials: AwsCredentials,
    region: str,
    service: str,
    now: datetime,
) -> dict[str, str]:
    """``headers`` plus ``x-amz-date``, the session token when there is one, and ``authorization``."""
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = amz_date[:8]
    out = {k.lower(): v for k, v in headers.items()}
    out["host"] = out.get("host") or _host(url)
    out["x-amz-date"] = amz_date
    if credentials.session_token:
        out["x-amz-security-token"] = credentials.session_token
    signed = sorted(name for name in out if name not in _UNSIGNED)
    canonical_headers = "".join(f"{name}:{' '.join(out[name].split())}\n" for name in signed)
    parts = urlsplit(url)
    canonical_request = "\n".join(
        [
            method.upper(),
            quote(parts.path or "/", safe="/~"),
            _canonical_query(parts.query),
            canonical_headers,
            ";".join(signed),
            hashlib.sha256(body).hexdigest(),
        ]
    )
    scope = f"{date}/{region}/{service}/aws4_request"
    to_sign = "\n".join([ALGORITHM, amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()])
    signature = hmac.new(
        _signing_key(credentials.secret_access_key, date, region, service), to_sign.encode(), hashlib.sha256
    ).hexdigest()
    out["authorization"] = (
        f"{ALGORITHM} Credential={credentials.access_key_id}/{scope}, "
        f"SignedHeaders={';'.join(signed)}, Signature={signature}"
    )
    return out


__all__ = ["ALGORITHM", "AwsCredentials", "sign"]
