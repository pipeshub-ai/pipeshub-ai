"""Certificates for etcd's TLS client authentication (SEC-D-06), written by Compose's one-shot ``etcd-tls`` service.

etcd accepts a client only with a certificate its own CA signed (``--client-cert-auth``), so a container that can
reach it on ``pipeshub`` or ``sandbox_kv`` but holds no certificate cannot read or write a key. The CA's private key
stays in its own volume, which only this service mounts. Each consumer mounts one folder holding the CA certificate
and its own certificate and key:

    server/          etcd itself (serverAuth, and clientAuth for its healthcheck)
    pipeshub-ai/     the app
    egress-proxy/    the sandbox plane, one identity each: the certificate's CN names the component, which is the
    sandbox-broker/  user name etcd's role-based auth reads once it is enabled (R38IDENT-FU-9)

Every run keeps what is still good and issues only what is missing, near expiry, for another CA or for other
names, so running it on every start is the renewal. Writes are atomic (a temporary file, then a rename).
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_VALIDITY = timedelta(days=3650)
LEAF_VALIDITY = timedelta(days=1825)
# Renewed well before expiry: a stack that is never restarted still has a year of margin after the last renewal.
LEAF_RENEW_BEFORE = timedelta(days=365)

CA_CERT, CA_KEY = "ca.crt", "ca.key"
LEAF_CERT, LEAF_KEY = "tls.crt", "tls.key"


@dataclass(frozen=True, slots=True)
class Leaf:
    folder: str
    common_name: str
    dns_names: tuple[str, ...] = ()
    ip_addresses: tuple[str, ...] = ()
    server: bool = False


ETCD_LEAVES: tuple[Leaf, ...] = (
    Leaf("server", "etcd", dns_names=("etcd", "localhost"), ip_addresses=("127.0.0.1",), server=True),
    Leaf("pipeshub-ai", "pipeshub-ai"),
    Leaf("egress-proxy", "egress-proxy"),
    Leaf("sandbox-broker", "sandbox-broker"),
)


@dataclass(frozen=True, slots=True)
class Owner:
    uid: int
    gid: int

    @classmethod
    def parse(cls, value: str) -> Owner:
        uid, sep, gid = value.partition(":")
        gid = gid if sep else uid
        if not (uid.isascii() and uid.isdigit() and gid.isascii() and gid.isdigit()):
            raise ValueError(f"owner must be uid:gid, got {value!r}")
        return cls(int(uid), int(gid))


def _write(path: Path, data: bytes, mode: int, owner: Owner | None) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, data)
        os.fchmod(fd, mode)
        if owner is not None:
            os.fchown(fd, owner.uid, owner.gid)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PipesHub"), x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def ensure_ca(folder: Path, *, now: datetime) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    cert_path, key_path = folder / CA_CERT, folder / CA_KEY
    if cert_path.exists() and key_path.exists():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        if isinstance(key, ec.EllipticCurvePrivateKey) and cert.not_valid_after_utc > now:
            return cert, key
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name("PipesHub internal CA"))
        .issuer_name(_name("PipesHub internal CA"))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + CA_VALIDITY)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    folder.mkdir(parents=True, exist_ok=True)
    _write(key_path, _key_pem(key), 0o600, None)
    _write(cert_path, cert.public_bytes(serialization.Encoding.PEM), 0o644, None)
    return cert, key


def _subject_alt_names(leaf: Leaf) -> list[x509.GeneralName]:
    names: list[x509.GeneralName] = [x509.DNSName(n) for n in leaf.dns_names]
    names += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in leaf.ip_addresses]
    return names or [x509.DNSName(leaf.common_name)]


def _leaf_is_current(folder: Path, leaf: Leaf, ca: x509.Certificate, *, now: datetime) -> bool:
    cert_path, key_path, ca_path = folder / LEAF_CERT, folder / LEAF_KEY, folder / CA_CERT
    if not (cert_path.exists() and key_path.exists() and ca_path.exists()):
        return False
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        if ca_path.read_bytes() != ca.public_bytes(serialization.Encoding.PEM):
            return False
        cert.verify_directly_issued_by(ca)
        sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except (ValueError, TypeError, x509.ExtensionNotFound, InvalidSignature):
        return False
    return (
        cert.subject == _name(leaf.common_name)
        and list(sans) == _subject_alt_names(leaf)
        and cert.not_valid_after_utc - now > LEAF_RENEW_BEFORE
    )


def ensure_leaf(
    root: Path,
    leaf: Leaf,
    ca: tuple[x509.Certificate, ec.EllipticCurvePrivateKey],
    *,
    owner: Owner | None,
    now: datetime,
) -> bool:
    """Issues ``leaf`` into ``root/leaf.folder`` unless a current one is there; True when it issued."""
    ca_cert, ca_key = ca
    folder = root / leaf.folder
    folder.mkdir(parents=True, exist_ok=True)
    if _leaf_is_current(folder, leaf, ca_cert, now=now):
        return False
    key = ec.generate_private_key(ec.SECP256R1())
    usages = [ExtendedKeyUsageOID.CLIENT_AUTH] + ([ExtendedKeyUsageOID.SERVER_AUTH] if leaf.server else [])
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(leaf.common_name))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(min(now + LEAF_VALIDITY, ca_cert.not_valid_after_utc))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True)
        .add_extension(x509.ExtendedKeyUsage(usages), critical=False)
        .add_extension(x509.SubjectAlternativeName(_subject_alt_names(leaf)), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    # Key first: a consumer that starts between the two renames then finds a new key with the old certificate at
    # worst, which it reports, rather than a certificate whose key it does not have.
    _write(folder / LEAF_KEY, _key_pem(key), 0o600, owner)
    _write(folder / LEAF_CERT, cert.public_bytes(serialization.Encoding.PEM), 0o644, owner)
    _write(folder / CA_CERT, ca_cert.public_bytes(serialization.Encoding.PEM), 0o644, owner)
    return True


def ensure_etcd_tls(
    root: Path,
    *,
    server_owner: Owner | None,
    client_owner: Owner | None,
    now: datetime | None = None,
) -> list[str]:
    """Writes or renews every etcd identity under ``root``; returns the folders it issued."""
    now = now or datetime.now(UTC)
    ca = ensure_ca(root / "ca", now=now)
    return [
        leaf.folder
        for leaf in ETCD_LEAVES
        if ensure_leaf(root, leaf, ca, owner=server_owner if leaf.server else client_owner, now=now)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path, help="folder holding ca/, server/ and one folder per client")
    parser.add_argument("--server-owner", default=os.getenv("ETCD_TLS_SERVER_OWNER", "0:0"))
    parser.add_argument("--client-owner", default=os.getenv("ETCD_TLS_CLIENT_OWNER", "1000:1000"))
    args = parser.parse_args(argv)
    chown = os.geteuid() == 0
    issued = ensure_etcd_tls(
        args.root,
        server_owner=Owner.parse(args.server_owner) if chown else None,
        client_owner=Owner.parse(args.client_owner) if chown else None,
    )
    sys.stderr.write(f"etcd TLS: issued {', '.join(issued)}\n" if issued else "etcd TLS: every certificate is current\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
