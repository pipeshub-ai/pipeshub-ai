"""SP-1..SP-4 (P5.9b): what a snapshot may hold, the archive check, the secret scan and the envelope."""

from __future__ import annotations

import io
import tarfile

import pytest

from harness_core.engines.errors import SnapshotRefused, SnapshotUnavailable
from harness_core.engines.snapshots import (
    RUN_SECRET,
    SNAPSHOT_CORRUPT,
    SNAPSHOT_FAILED,
    SNAPSHOT_FOREIGN,
    SNAPSHOT_TOO_LARGE,
    SNAPSHOT_UNSAFE,
    SnapshotPolicy,
    pack,
    read_archive,
    secret_kinds,
    unpack,
)
from harness_core.security.token_patterns import VENDOR_TOKEN_PATTERNS
from harness_core.testing.contracts.sandbox_snapshots import tar_of
from harness_core.workspace.types import ObjectScope
from harness_engines.release import release_snapshot_policy

RESERVED = ("/workspace", "/state", "/skills")
POLICY = SnapshotPolicy(("/tmp/.cache", "/tmp/matplotlib"), max_bytes=1024)
SCOPE = ObjectScope("org-1", "ws:session-1")


@pytest.mark.parametrize(
    "paths",
    [
        ("tmp/.cache",),
        ("/tmp/../workspace",),
        ("/tmp/./x",),
        ("/",),
        ("/tmp",),
        ("/tmp/a b",),
        ("/tmp/$(id)",),
        ("/tmp/x/",),
        ("/tmp/x", "/tmp/x"),
        ("/tmp/x", "/tmp/x/y"),
        (),
    ],
)
def test_sp1_a_policy_refuses_paths_it_cannot_keep_safely(paths: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        _ = SnapshotPolicy(paths)


@pytest.mark.parametrize("path", ["/workspace/.cache", "/state/x", "/skills/a/b"])
def test_sp1_a_policy_never_reaches_the_workspace_state_or_skills(path: str) -> None:
    with pytest.raises(ValueError):
        SnapshotPolicy((path,)).check_reserved(RESERVED)


def test_sp1_a_policy_holding_a_reserved_root_is_refused() -> None:
    with pytest.raises(ValueError):
        SnapshotPolicy(("/tmp/x",)).check_reserved(("/tmp/x/workspace",))


def test_sp1_cap_must_be_positive() -> None:
    with pytest.raises(ValueError):
        _ = SnapshotPolicy(("/tmp/x",), max_bytes=0)


def test_sp1_the_release_policy_is_valid_and_keeps_out_of_engine_state() -> None:
    policy = release_snapshot_policy()
    policy.check_reserved(RESERVED)
    assert "/tmp/oc-state" not in policy.paths
    assert all(p.startswith("/tmp/") for p in policy.paths)


def _tar(add: list[tarfile.TarInfo], contents: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for info in add:
            data = (contents or {}).get(info.name)
            if data is not None:
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            else:
                archive.addfile(info)
    return buffer.getvalue()


def _member(name: str, kind: bytes = tarfile.REGTYPE, linkname: str = "") -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type, info.linkname = kind, linkname
    return info


@pytest.mark.parametrize(
    "name",
    ["workspace/x", "/tmp/.cache/x", "tmp/.cache/../../workspace/x", "tmp/.cachex/y", "tmp", "state/opencode/db"],
)
def test_sp2_a_member_outside_the_policy_refuses_the_archive(name: str) -> None:
    with pytest.raises(SnapshotRefused) as refused:
        _ = read_archive(_tar([_member(name)], {name: b"x"}), POLICY)
    assert refused.value.reason_code == SNAPSHOT_UNSAFE


@pytest.mark.parametrize(
    "member",
    [
        _member("tmp/.cache/link", tarfile.SYMTYPE, "/workspace/secret"),
        _member("tmp/.cache/hard", tarfile.LNKTYPE, "tmp/.cache/a"),
        _member("tmp/.cache/dev", tarfile.CHRTYPE),
        _member("tmp/.cache/fifo", tarfile.FIFOTYPE),
    ],
)
def test_sp2_links_devices_and_fifos_are_left_out(member: tarfile.TarInfo) -> None:
    archive = read_archive(_tar([_member("tmp/.cache/a"), member], {"tmp/.cache/a": b"a"}), POLICY)
    assert [e.name for e in archive.entries] == ["tmp/.cache/a"]
    assert archive.skipped == 1


def test_sp2_files_over_the_cap_refuse_the_archive() -> None:
    with pytest.raises(SnapshotRefused) as refused:
        _ = read_archive(tar_of({"tmp/.cache/a": b"a" * 600, "tmp/matplotlib/b": b"b" * 600}), POLICY)
    assert refused.value.reason_code == SNAPSHOT_TOO_LARGE


def test_sp2_a_clean_archive_keeps_files_folders_and_modes_and_rewrites_identically() -> None:
    data = tar_of({"tmp/.cache/a/b.json": b"{}"}, folders=["tmp/.cache", "tmp/.cache/a", "tmp/matplotlib"])
    archive = read_archive(data, POLICY)
    assert [(e.name, e.data) for e in archive.entries] == [
        ("tmp/.cache", None), ("tmp/.cache/a", None), ("tmp/matplotlib", None), ("tmp/.cache/a/b.json", b"{}"),
    ]
    assert (archive.size, archive.files) == (2, 1)
    assert read_archive(archive.to_tar(), POLICY) == archive
    assert archive.to_tar() == read_archive(archive.to_tar(), POLICY).to_tar()


def test_sp2_a_broken_archive_is_a_failed_snapshot() -> None:
    with pytest.raises(SnapshotRefused) as refused:
        _ = read_archive(b"not a tar" * 100, POLICY)
    assert refused.value.reason_code == SNAPSHOT_FAILED


SAMPLES = {
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAK\n-----END RSA PRIVATE KEY-----",
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJlLXZhbHVl",
    "anthropic_key": "sk-ant-api03-abcdefghijklmnopqrstuvwx",
    "openai_key": "sk-proj-abcdefghijklmnopqrstuvwxyz012345",
    "slack_token": "xoxb-1234567890-abcdefghij",
    "github_token": "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "gitlab_token": "glpat-abcdefghijklmnopqrst",
    "aws_access_key": "AKIAABCDEFGHIJKLMNOP",
    "google_api_key": "AIza" + "a" * 35,
    "pipeshub_pat": "phpat_abcdefghijklmnopqrstuv",
}


def test_sp3_every_shared_pattern_has_a_sample() -> None:
    assert set(SAMPLES) == {p.kind for p in VENDOR_TOKEN_PATTERNS}


@pytest.mark.parametrize("kind", sorted(SAMPLES))
def test_sp3_a_credential_in_content_is_found(kind: str) -> None:
    content = b"\x00\x01binary\xff" + SAMPLES[kind].encode() + b"\x00"
    archive = read_archive(tar_of({"tmp/.cache/x/config": content}), SnapshotPolicy(("/tmp/.cache",)))
    assert kind in secret_kinds(archive)


def test_sp3_a_credential_in_a_member_name_is_found() -> None:
    name = "tmp/.cache/" + SAMPLES["github_token"]
    archive = read_archive(tar_of({name: b"x"}), SnapshotPolicy(("/tmp/.cache",)))
    assert secret_kinds(archive) == ("github_token",)


@pytest.mark.parametrize("secret", ["pxh-model-0123456789abcdef0123456789abcdef", "attempt-token.value.here"])
def test_sp3_the_runs_own_values_are_found(secret: str) -> None:
    archive = read_archive(tar_of({"tmp/.cache/x": b"key=" + secret.encode()}), POLICY)
    assert secret_kinds(archive, (secret,)) == (RUN_SECRET,)
    assert secret_kinds(archive, ("other-value-entirely",)) == ()


def test_sp3_a_clean_archive_and_short_values_find_nothing() -> None:
    archive = read_archive(tar_of({"tmp/.cache/x": b"plain cache data sk-short"}), POLICY)
    assert secret_kinds(archive, ("cache", "")) == ()


def test_sp4_the_envelope_round_trips_in_its_own_session() -> None:
    data = tar_of({"tmp/.cache/a": b"a"})
    assert unpack(SCOPE, pack(SCOPE, data)) == data


@pytest.mark.parametrize("other", [ObjectScope("org-1", "ws:session-2"), ObjectScope("org-2", "ws:session-1")])
def test_sp4_another_session_or_tenant_is_foreign(other: ObjectScope) -> None:
    with pytest.raises(SnapshotUnavailable) as refused:
        _ = unpack(other, pack(SCOPE, b"x"))
    assert refused.value.reason_code == SNAPSHOT_FOREIGN


@pytest.mark.parametrize(
    "payload",
    [b"garbage", b"PXSNAP1\n{not json\n", b"PXSNAP1\n[]\n", pack(SCOPE, b"x" * 1000)[:-5], pack(SCOPE, b"x") + b"\x00junk"],
)
def test_sp4_garbage_or_truncation_is_corrupt(payload: bytes) -> None:
    with pytest.raises(SnapshotUnavailable) as refused:
        _ = unpack(SCOPE, payload)
    assert refused.value.reason_code == SNAPSHOT_CORRUPT
