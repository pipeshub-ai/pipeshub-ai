"""Key-pair authentication for the Snowflake SQL API.

The SQL API takes a JWT signed with the user's RSA private key; Snowflake
checks it against the public key set on the user. The claims follow
Snowflake's documented recipe, as its Python connector builds them:
https://docs.snowflake.com/en/developer-guide/sql-api/authenticating
"""

from __future__ import annotations

import base64
import hashlib
import re
import textwrap
import time
from typing import TYPE_CHECKING

import jwt
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

if TYPE_CHECKING:
    from collections.abc import Callable

# Snowflake accepts a JWT for at most an hour.
TOKEN_LIFETIME_S = 59 * 60
# A token with less than this left is signed again before a request.
REFRESH_MARGIN_S = 5 * 60
MIN_KEY_BITS = 2048

_DOMAIN = ".snowflakecomputing.com"
_ACCOUNT_PATTERN = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$")
# A second label that is no organization's account name: connectivity and cloud
# suffixes, and the one region ID without a hyphen.
_NON_REGION_LABELS = frozenset({"privatelink", "global", "aws", "azure", "gcp", "mexicocentral"})
_PEM_BLOCK = re.compile(r"-----BEGIN ([A-Z ]+)-----(.*?)-----END \1-----", re.DOTALL)


def account_host_label(account_identifier: str) -> str:
    """The account part of the SQL API host: ``acme-prod``, ``xy12345.us-east-1``.

    Accepts the identifier, the host or a URL. Raises ValueError for anything
    that cannot be an account (the same string builds the URL and the JWT).
    """
    text = account_identifier.strip()
    text = re.sub(r"^[a-zA-Z]+://", "", text).split("/", 1)[0]
    if text.lower().endswith(_DOMAIN):
        text = text[: -len(_DOMAIN)]
    if not text or not _ACCOUNT_PATTERN.match(text):
        raise ValueError(
            "The Snowflake account identifier is not valid. Use orgname-accountname "
            "(for example acme-prod) or the account locator with its region (for example xy12345.us-east-1)."
        )
    labels = text.split(".")
    # Only a two-label form can be orgname.accountname; longer locators carry a
    # government segment (fhplus, dod) before the region.
    if len(labels) == 2 and "-" not in labels[1] and labels[1].lower() not in _NON_REGION_LABELS:
        # orgname.accountname: the dotted form names neither a host nor a JWT issuer.
        raise ValueError(
            f"Write the account as {labels[0]}-{labels[1]} (organization and account joined by a hyphen)."
        )
    return text


def jwt_account(account_identifier: str) -> str:
    """The account as the JWT names it: Snowflake's rule, upper-cased.

    Without ``.global``, the part before the first ``.`` (a locator loses its
    region); with it, the part before the first ``-``.
    """
    label = account_host_label(account_identifier)
    if ".global" in label.lower():
        return label.split("-", 1)[0].upper()
    return label.split(".", 1)[0].upper()


def _normalise_pem(pem: str) -> bytes:
    """A PEM pasted onto one line, or with stray spaces, rebuilt with its line breaks."""
    text = pem.strip().replace("\r\n", "\n")
    match = _PEM_BLOCK.search(text)
    if match is None:
        return text.encode()
    label, body = match.group(1), re.sub(r"\s+", "", match.group(2))
    lines = "\n".join(textwrap.wrap(body, 64))
    return f"-----BEGIN {label}-----\n{lines}\n-----END {label}-----\n".encode()


def load_private_key(pem: str, passphrase: str | None = None) -> rsa.RSAPrivateKey:
    """The RSA private key in ``pem`` (PKCS#8 or PKCS#1, encrypted or not).

    Raises ValueError with a message for the user; never one that holds the key.
    """
    if not pem or not pem.strip():
        raise ValueError("The Snowflake private key is missing.")
    password = passphrase.encode() if passphrase else None
    try:
        key = serialization.load_pem_private_key(_normalise_pem(pem), password=password)
    except TypeError:
        if password is None:
            raise ValueError("The private key is encrypted: enter its passphrase.") from None
        raise ValueError("The private key is not encrypted: leave the passphrase empty.") from None
    except (ValueError, UnsupportedAlgorithm):
        if password is not None:
            raise ValueError(
                "The private key could not be read: check the passphrase and that the key is a PEM "
                "(-----BEGIN PRIVATE KEY----- or -----BEGIN ENCRYPTED PRIVATE KEY-----)."
            ) from None
        raise ValueError(
            "The private key could not be read: paste the whole PEM, including its BEGIN and END lines."
        ) from None
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("Snowflake's SQL API needs an RSA key; this key is not RSA.")
    if key.key_size < MIN_KEY_BITS:
        raise ValueError(f"The RSA key has {key.key_size} bits; Snowflake needs at least {MIN_KEY_BITS}.")
    return key


def public_key_fingerprint(key: rsa.RSAPrivateKey) -> str:
    """``SHA256:<base64>`` of the public key, as ``DESC USER`` shows it in ``RSA_PUBLIC_KEY_FP``."""
    der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return "SHA256:" + base64.b64encode(hashlib.sha256(der).digest()).decode()


class KeyPairSigner:
    """Signs the SQL API's JWT, and signs again when the last one is near expiry."""

    def __init__(
        self,
        account_identifier: str,
        user: str,
        private_key: rsa.RSAPrivateKey,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not user or not user.strip():
            raise ValueError("The Snowflake user is missing.")
        self.account = jwt_account(account_identifier)
        # The login name, upper-cased: Snowflake compares it so.
        self.user = user.strip().upper()
        self.fingerprint = public_key_fingerprint(private_key)
        self._key = private_key
        self._clock = clock
        self._token = ""
        self._expires_at = 0.0

    def token(self) -> str:
        now = self._clock()
        if not self._token or self._expires_at - now < REFRESH_MARGIN_S:
            issued = int(now)
            subject = f"{self.account}.{self.user}"
            self._token = jwt.encode(
                {
                    "iss": f"{subject}.{self.fingerprint}",
                    "sub": subject,
                    "iat": issued,
                    "exp": issued + TOKEN_LIFETIME_S,
                },
                self._key,
                algorithm="RS256",
            )
            self._expires_at = issued + TOKEN_LIFETIME_S
        return self._token

    def __repr__(self) -> str:
        return f"KeyPairSigner(account={self.account!r}, user={self.user!r}, fingerprint={self.fingerprint!r})"
