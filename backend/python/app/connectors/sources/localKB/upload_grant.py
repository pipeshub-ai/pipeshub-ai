"""Signed grant for the records and storage documents of one knowledge-base upload.

The connector service never trusts a storage document id from a request body. The gateway
is the only party that creates storage documents; once it has, it signs the ids it created
together with the record key, detected MIME type and extension each belongs to and the org, the user, the KB and the folder they were
created for, and sends the result next to the files. The connector takes every storage pointer and the
record key from the grant, never from the body.

Wire form: ``<b64url(claims JSON)>.<b64url(HMAC-SHA256(key, <b64url(claims JSON)>))>``. The
key is ``HMAC-SHA256(scopedJwtSecret, "pipeshub/kb-upload-grant/v1")``: the existing service
secret, domain-separated so a grant cannot be a service token or the reverse. The Node
signer is ``libs/utils/kbUploadGrant.ts``.
"""

import base64
import hashlib
import hmac
import json
import re
import time
from collections.abc import Iterable, Mapping
from typing import Any, NamedTuple

from app.config.constants.service import config_node_constants

GRANT_CONTEXT = b"pipeshub/kb-upload-grant/v1"
GRANT_VERSION = 1
DEFAULT_TTL_SECONDS = 300
MAX_TTL_SECONDS = 300
MAX_FUTURE_SKEW_SECONDS = 60
MAX_DOCUMENT_IDS = 5000
CLAIM_FIELDS = frozenset({"v", "orgId", "userId", "kbId", "folderId", "documents", "iat", "exp"})
_SIG = re.compile(r"[A-Za-z0-9_-]{43}")  # a base64url SHA-256 MAC: no padding, no suffix


class GrantedDocument(NamedTuple):
    """What the gateway created and saw for one file: its storage document, MIME type and extension."""

    document_id: str
    mime_type: str
    extension: str  # as the gateway computed it from the file name; "" for none


class UploadGrantError(Exception):
    """The grant is missing, malformed, forged, expired or bound to something else."""


def derive_grant_key(scoped_jwt_secret: str) -> bytes:
    if not scoped_jwt_secret:
        raise UploadGrantError("no signing secret")
    return hmac.new(scoped_jwt_secret.encode(), GRANT_CONTEXT, hashlib.sha256).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_grant(
    scoped_jwt_secret: str,
    *,
    org_id: str,
    user_id: str,
    kb_id: str,
    folder_id: str | None,
    documents: Mapping[str, GrantedDocument] | Iterable[tuple[str, GrantedDocument]],
    now: int | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
    """Signer with the same wire form as the gateway's; used by tests."""
    issued = int(time.time()) if now is None else now
    claims = {
        "v": GRANT_VERSION, "orgId": org_id, "userId": user_id, "kbId": kb_id, "folderId": folder_id or "",
        "documents": sorted([k, g.document_id, g.mime_type, g.extension] for k, g in dict(documents).items()), "iat": issued, "exp": issued + ttl_seconds,
    }
    body = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    mac = hmac.new(derive_grant_key(scoped_jwt_secret), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(mac)}"


def _unb64_strict(text: str) -> bytes:
    raw = _unb64(text)
    if _b64(raw) != text:  # the one canonical encoding, so a grant has one string form
        raise UploadGrantError("malformed grant")
    return raw


def verify_grant(
    scoped_jwt_secret: str,
    token: Any,  # noqa: ANN401 - straight from a request body
    *,
    org_id: str,
    user_id: str,
    kb_id: str,
    folder_id: str | None,
    now: int | None = None,
) -> dict[str, GrantedDocument]:
    """The grant's ``{record key: GrantedDocument}``. Raises UploadGrantError on anything else."""
    if not isinstance(token, str) or token.count(".") != 1:
        raise UploadGrantError("missing or malformed grant")
    body, sig = token.split(".")
    if not _SIG.fullmatch(sig):
        raise UploadGrantError("malformed grant")
    want = _b64(hmac.new(derive_grant_key(scoped_jwt_secret), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(want.encode(), sig.encode()):
        raise UploadGrantError("bad signature")
    try:
        claims = json.loads(_unb64_strict(body))
    except Exception:
        raise UploadGrantError("malformed grant") from None
    if not isinstance(claims, dict) or set(claims) != CLAIM_FIELDS:
        raise UploadGrantError("malformed grant")
    if type(claims["v"]) is not int or claims["v"] != GRANT_VERSION:
        raise UploadGrantError("unsupported grant")
    current = int(time.time()) if now is None else now
    iat, exp = claims.get("iat"), claims.get("exp")
    if not isinstance(iat, int) or not isinstance(exp, int) or isinstance(iat, bool) or isinstance(exp, bool):
        raise UploadGrantError("malformed grant")
    if exp <= current:
        raise UploadGrantError("expired")
    # The gateway signs the full lifetime, so a gateway clock ahead of ours by up to the skew must still verify.
    if iat > current + MAX_FUTURE_SKEW_SECONDS or exp - iat > MAX_TTL_SECONDS:
        raise UploadGrantError("invalid lifetime")
    bound = {"orgId": org_id, "userId": user_id, "kbId": kb_id, "folderId": folder_id or ""}
    for field, expected in bound.items():
        if (not expected and field != "folderId") or claims.get(field) != expected:  # the KB root has no folder
            raise UploadGrantError(f"grant is not for this {field}")
    pairs = claims["documents"]
    if not isinstance(pairs, list) or not pairs or len(pairs) > MAX_DOCUMENT_IDS:
        raise UploadGrantError("malformed grant")
    documents: dict[str, GrantedDocument] = {}
    for triple in pairs:
        if not (
            isinstance(triple, list) and len(triple) == 4 and all(isinstance(x, str) for x in triple) and all(triple[:3])
        ) or triple[0] in documents:
            raise UploadGrantError("malformed grant")
        documents[triple[0]] = GrantedDocument(triple[1], triple[2], triple[3])
    if len({g.document_id for g in documents.values()}) != len(documents):
        raise UploadGrantError("malformed grant")
    return documents


async def load_grant_secret(config_service: Any) -> str:  # noqa: ANN401
    secrets = await config_service.get_config(config_node_constants.SECRET_KEYS.value, use_cache=True)
    secret = (secrets or {}).get("scopedJwtSecret")
    if not secret:
        raise UploadGrantError("no signing secret")
    return secret
