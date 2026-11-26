"""The proxy's signing certificate for TLS it ends itself (OWD-1 "TLS interception", WP-06).

A program in a sandbox that holds only a placeholder still speaks HTTPS to its service. For a destination with a
credential route (``Basic`` or substituted, never a gateway) the proxy answers the ``CONNECT`` itself, ends the TLS
with a certificate for that host signed by this authority, puts the credential on each request inside, and opens its
own TLS to the real host, verified as usual. A sandbox trusts the authority only when it reads its certificate from
the proxy (``EGRESS_CA_PATH``); the private key never leaves the proxy, and the certificate is not a secret.

The authority is one per deployment when the proxy keeps durable state (every replica and restart signs with the
same key, so a long-lived MCP runner keeps trusting it), else one per process.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import ssl
import tempfile
import threading
from typing import TYPE_CHECKING, Final

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.clock import Clock
    from harness_core.sandbox.egress.ports import EgressStateStore
    from harness_core.sandbox.egress.state import StateSealer

AUTHORITY_NAME: Final = "PipesHub egress proxy"
AUTHORITY_DAYS: Final = 3650
LEAF_DAYS: Final = 7
# A host's certificate is minted again once it has less than this left.
LEAF_RENEW: Final = dt.timedelta(days=1)
_BACKDATE: Final = dt.timedelta(hours=1)


def _key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


class EgressAuthority:
    """Signs a certificate per intercepted host; ``server_context`` is what the proxy ends a sandbox's TLS with."""

    def __init__(self, certificate_pem: bytes, key_pem: bytes, clock: Clock) -> None:
        self._certificate = x509.load_pem_x509_certificate(certificate_pem)
        key = serialization.load_pem_private_key(key_pem, password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise ValueError("the egress authority's key is not an EC key")
        self._key = key
        self._clock = clock
        self._leaf_key = ec.generate_private_key(ec.SECP256R1())
        self._contexts: dict[str, tuple[ssl.SSLContext, dt.datetime]] = {}
        self._lock = threading.Lock()

    @classmethod
    def generate(cls, clock: Clock) -> EgressAuthority:
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, AUTHORITY_NAME)])
        now = clock.now()
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - _BACKDATE)
            .not_valid_after(now + dt.timedelta(days=AUTHORITY_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=False,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256())
        )
        return cls(certificate.public_bytes(serialization.Encoding.PEM), _key_pem(key), clock)

    @property
    def certificate_pem(self) -> bytes:
        return self._certificate.public_bytes(serialization.Encoding.PEM)

    def key_pem(self) -> bytes:
        """For the proxy's sealed durable state only."""
        return _key_pem(self._key)

    def leaf(self, host: str) -> x509.Certificate:
        now = self._clock.now()
        ski = self._certificate.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
        return (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
            .issuer_name(self._certificate.subject)
            .public_key(self._leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - _BACKDATE)
            .not_valid_after(now + dt.timedelta(days=LEAF_DAYS))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(self._leaf_key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ski), critical=False)
            .sign(self._key, hashes.SHA256())
        )

    def server_context(self, host: str) -> ssl.SSLContext:
        """A TLS server context presenting ``host``'s certificate, minted once and again before it lapses."""
        with self._lock:
            held = self._contexts.get(host)
            if held is not None and held[1] - self._clock.now() > LEAF_RENEW:
                return held[0]
            leaf = self.leaf(host)
            context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.sni_callback = _same_name(host)
            chain = leaf.public_bytes(serialization.Encoding.PEM) + self.certificate_pem
            # The ssl module loads a chain only from files: a private folder, removed before this returns.
            with tempfile.TemporaryDirectory(prefix="pxh-egress-") as folder:
                os.chmod(folder, 0o700)
                cert_path, key_path = os.path.join(folder, "chain.pem"), os.path.join(folder, "key.pem")
                with open(os.open(cert_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as out:
                    _ = out.write(chain)
                with open(os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as out:
                    _ = out.write(_key_pem(self._leaf_key))
                context.load_cert_chain(cert_path, key_path)
            self._contexts[host] = (context, leaf.not_valid_after_utc)
            return context


def _same_name(host: str) -> Callable[[ssl.SSLObject, str | None, ssl.SSLContext], int | None]:
    """The handshake check of a connection ended for ``host``: the TLS name the client asks for is that host, so
    a request is never fronted behind another name than the one the ``CONNECT`` was checked for (WP-63)."""

    def check(_conn: ssl.SSLObject, server_name: str | None, _context: ssl.SSLContext) -> int | None:
        if server_name is None or server_name.lower().rstrip(".") != host:
            return ssl.ALERT_DESCRIPTION_UNRECOGNIZED_NAME
        return None

    return check


async def load_authority(
    clock: Clock, store: EgressStateStore | None = None, sealer: StateSealer | None = None
) -> EgressAuthority:
    """The deployment's authority from the proxy's durable state (the first replica's), else one for this process."""
    if store is None or sealer is None:
        return EgressAuthority.generate(clock)
    from harness_core.sandbox.egress.state import AUTHORITY_KEY, StateUnverifiable

    made = EgressAuthority.generate(clock)
    proposed = sealer.seal(
        AUTHORITY_KEY,
        json.dumps({"certificate": made.certificate_pem.decode("ascii"), "key": made.key_pem().decode("ascii")}).encode(),
    )
    held = await store.put_if_absent(AUTHORITY_KEY, proposed)
    if held == proposed:
        return made
    try:
        body = json.loads(sealer.open(AUTHORITY_KEY, held))
        return EgressAuthority(body["certificate"].encode("ascii"), body["key"].encode("ascii"), clock)
    except (StateUnverifiable, ValueError, KeyError, TypeError):
        # Sealed under another deployment secret, or damaged: a new authority; sandboxes read it again on start.
        await store.put(AUTHORITY_KEY, proposed, ttl_s=None)
        return made


__all__ = ["AUTHORITY_NAME", "EgressAuthority", "load_authority"]
