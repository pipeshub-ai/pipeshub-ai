"""SEC-D-06: etcd's certificates from Compose's etcd-tls service (app/utils/internal_tls.py)."""

from __future__ import annotations

import os
import stat
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.utils.internal_tls import (
    ETCD_LEAVES,
    LEAF_RENEW_BEFORE,
    LEAF_VALIDITY,
    Leaf,
    Owner,
    ensure_ca,
    ensure_etcd_tls,
    ensure_leaf,
    main,
)

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 11, 13, tzinfo=UTC)


def _cert(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


def _usages(cert: x509.Certificate) -> set[x509.ObjectIdentifier]:
    return set(cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value)


def test_every_identity_is_issued_by_one_ca_with_its_own_name(tmp_path: Path) -> None:
    issued = ensure_etcd_tls(tmp_path, server_owner=None, client_owner=None, now=NOW)

    assert issued == [leaf.folder for leaf in ETCD_LEAVES]
    ca = _cert(tmp_path / "ca" / "ca.crt")
    for leaf in ETCD_LEAVES:
        cert = _cert(tmp_path / leaf.folder / "tls.crt")
        cert.verify_directly_issued_by(ca)
        assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == leaf.common_name
        assert (tmp_path / leaf.folder / "ca.crt").read_bytes() == (tmp_path / "ca" / "ca.crt").read_bytes()


def test_only_the_server_may_serve_and_every_identity_may_sign_in(tmp_path: Path) -> None:
    ensure_etcd_tls(tmp_path, server_owner=None, client_owner=None, now=NOW)

    server = _cert(tmp_path / "server" / "tls.crt")
    assert _usages(server) == {ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH}
    sans = server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert {"etcd", "localhost"} <= set(sans.get_values_for_type(x509.DNSName))
    for folder in ("pipeshub-ai", "egress-proxy", "sandbox-broker"):
        assert _usages(_cert(tmp_path / folder / "tls.crt")) == {ExtendedKeyUsageOID.CLIENT_AUTH}, folder


def test_the_ca_key_stays_in_the_ca_folder_and_every_key_is_owner_only(tmp_path: Path) -> None:
    ensure_etcd_tls(tmp_path, server_owner=None, client_owner=None, now=NOW)

    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("ca.key")) == ["ca/ca.key"]
    for key in tmp_path.rglob("*.key"):
        assert stat.S_IMODE(key.stat().st_mode) == 0o600, key
    assert not list(tmp_path.rglob(".*.tmp")), "a temporary file was left behind"


def test_a_second_run_keeps_every_current_certificate(tmp_path: Path) -> None:
    ensure_etcd_tls(tmp_path, server_owner=None, client_owner=None, now=NOW)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    assert ensure_etcd_tls(tmp_path, server_owner=None, client_owner=None, now=NOW + timedelta(days=30)) == []
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_a_certificate_near_expiry_is_renewed_and_the_rest_kept(tmp_path: Path) -> None:
    ensure_etcd_tls(tmp_path, server_owner=None, client_owner=None, now=NOW)
    near = NOW + LEAF_VALIDITY - LEAF_RENEW_BEFORE + timedelta(days=1)

    assert ensure_etcd_tls(tmp_path, server_owner=None, client_owner=None, now=near) == [leaf.folder for leaf in ETCD_LEAVES]
    assert _cert(tmp_path / "server" / "tls.crt").not_valid_after_utc > near + LEAF_RENEW_BEFORE


@pytest.mark.parametrize(
    "tamper",
    [
        "delete_key",
        "foreign_ca",
        "other_names",
        "garbage",
    ],
)
def test_a_certificate_that_is_not_ours_any_more_is_reissued(tmp_path: Path, tamper: str) -> None:
    """The shapes a later run must not trust: a lost key, another CA's certificate, other names, a corrupt file."""
    ensure_etcd_tls(tmp_path, server_owner=None, client_owner=None, now=NOW)
    folder = tmp_path / "pipeshub-ai"
    if tamper == "delete_key":
        (folder / "tls.key").unlink()
    elif tamper == "foreign_ca":
        other = tmp_path / "other"
        ensure_leaf(other, Leaf("pipeshub-ai", "pipeshub-ai"), ensure_ca(other / "ca", now=NOW), owner=None, now=NOW)
        (folder / "tls.crt").write_bytes((other / "pipeshub-ai" / "tls.crt").read_bytes())
    elif tamper == "other_names":
        ensure_leaf(tmp_path, Leaf("pipeshub-ai", "egress-proxy"), ensure_ca(tmp_path / "ca", now=NOW), owner=None, now=NOW)
    else:
        (folder / "tls.crt").write_bytes(b"-----BEGIN CERTIFICATE-----\nnot a certificate\n-----END CERTIFICATE-----\n")

    assert "pipeshub-ai" in ensure_etcd_tls(tmp_path, server_owner=None, client_owner=None, now=NOW)
    cert = _cert(folder / "tls.crt")
    cert.verify_directly_issued_by(_cert(tmp_path / "ca" / "ca.crt"))
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "pipeshub-ai"


def test_a_new_ca_reissues_every_identity(tmp_path: Path) -> None:
    ensure_etcd_tls(tmp_path, server_owner=None, client_owner=None, now=NOW)
    (tmp_path / "ca" / "ca.key").unlink()

    assert ensure_etcd_tls(tmp_path, server_owner=None, client_owner=None, now=NOW) == [leaf.folder for leaf in ETCD_LEAVES]


@pytest.mark.parametrize("value", ["1000", "1000:1000", "0:0", "4242:4243"])
def test_owner_parses_uid_and_gid(value: str) -> None:
    owner = Owner.parse(value)
    assert f"{owner.uid}:{owner.gid}" == (value if ":" in value else f"{value}:{value}")


@pytest.mark.parametrize("value", ["", "root", "1000:", "-1:0", "1000:abc", ":1000", "1000:1000:1000"])
def test_owner_refuses_anything_but_numbers(value: str) -> None:
    with pytest.raises(ValueError, match="uid:gid"):
        Owner.parse(value)


def test_main_chowns_only_as_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    chowned: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "fchown", lambda _fd, uid, gid: chowned.append((uid, gid)))
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    assert main([str(tmp_path / "a")]) == 0
    assert chowned == []

    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert main([str(tmp_path / "b"), "--server-owner", "0:0", "--client-owner", "4242:4243"]) == 0
    assert (0, 0) in chowned and (4242, 4243) in chowned
    assert set(chowned) == {(0, 0), (4242, 4243)}


def test_each_etcd_component_role_names_a_certificate_cn_compose_and_helm_issue() -> None:
    """R38IDENT-FU-9: Node creates an etcd user per sandbox-plane component named by its certificate's CN, with no
    password, so the user names in the shared contract must be the CNs etcd-tls (and the chart, for the broker) issue."""
    import json
    import re
    from pathlib import Path

    from app.utils.internal_tls import ETCD_LEAVES

    repo = Path(__file__).resolve().parents[5]
    contract = json.loads((repo / "contracts/secret-key/component-acl.json").read_text(encoding="utf-8"))
    leaves = {leaf.common_name for leaf in ETCD_LEAVES if not leaf.server}
    users = {row["user"] for row in contract["etcd"]}
    assert users <= leaves, users - leaves
    assert "pipeshub-ai" in leaves and "pipeshub-ai" not in users, "Node's own user is the root one, never a component's"
    chart = (repo / "deployment/helm/pipeshub-ai/templates/store-auth.yaml").read_text(encoding="utf-8")
    chart_cns = set(re.search(r'range \$role := list ((?:"[^"]+" ?)+)', chart).group(1).replace('"', "").split())
    assert "sandbox-broker" in chart_cns and "sandbox-broker" in users
    for row in contract["etcd"]:
        assert [p["permission"] for p in row["permissions"]] == ["Read"], "a component only reads etcd"
        assert all(p["rangeEnd"] == "" for p in row["permissions"]), "an exact key, never a prefix"
