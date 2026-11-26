"""LS-1..LS-8 (E5), LS-9, LS-10 (E0b), LS-11..LS-17 (X6 legal), LS-18..LS-21: the engine licence and binary scanner
(scripts/engines/licence_scan.py), offline."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import sys
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

REPO = Path(__file__).resolve().parents[5]
SCRIPT = REPO / "scripts" / "engines" / "licence_scan.py"
FIXTURES = Path(__file__).parent / "fixtures" / "licence"
CARGO_TREE = FIXTURES / "codex-rust-v0.160.0.cargo-tree.txt"
MPL_CRATES = {
    "nucleo@0.5.0",
    "nucleo-matcher@0.3.1",
    "option-ext@0.2.0",
    "symphonia@0.6.0",
    "symphonia-bundle-mp3@0.6.0",
    "symphonia-common@0.6.0",
    "symphonia-core@0.6.0",
    "symphonia-format-isomp4@0.6.0",
    "symphonia-format-mkv@0.6.0",
    "symphonia-format-ogg@0.6.0",
    "symphonia-format-riff@0.6.0",
    "symphonia-metadata@0.6.0",
}


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("engine_licence_scan", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scan = _load()


def _archive(path: Path, members: dict[str, bytes]) -> str:
    with tarfile.open(path, "w:gz") as archive:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return hashlib.sha256(path.read_bytes()).hexdigest()


APP_SERVER = b"\x7fELF" + bytes(range(256)) * 64 + b"Licensed under the Apache License, Version 2.0" + b"\0" * 4096
BWRAP = b"\x7fELF bubblewrap built for Codex\0 GNU Lesser General Public License v2 or later\0"


def _codex(tmp_path: Path, members: dict[str, bytes], *extra: str) -> object:
    asset = tmp_path / "codex-app-server-x86_64-unknown-linux-musl.tar.gz"
    digest = _archive(asset, members)
    return scan.run(
        ["codex", "0.160.0", "--asset", str(asset), "--sha256", digest, "--deps", str(CARGO_TREE), "--out", str(tmp_path / "out"), *extra]
    )


def test_ls1_reproduces_the_codex_rust_v0_160_0_audit(tmp_path: Path) -> None:
    report = _codex(tmp_path, {"codex-app-server": APP_SERVER})
    assert report.verdict == "pass_with_notices"
    assert report.failures == []
    assert report.blocked == []
    assert report.packages == 940
    assert {n["package"] for n in report.notices} == MPL_CRATES
    assert {n["licence"] for n in report.notices} == {"MPL-2.0"}
    assert all(n["source"].startswith("https://crates.io/crates/") for n in report.notices)
    self_cell = next(e for e in report.elections if e["package"].startswith("self_cell@"))
    assert (self_cell["licence"], self_cell["elected"]) == ("Apache-2.0 OR GPL-2.0-only", "Apache-2.0")
    assert report.markers == {}
    assert report.signature == "not_published"
    out = tmp_path / "out"
    saved = json.loads((out / "codex-0.160.0.licence.json").read_text())
    assert saved["verdict"] == "pass_with_notices"
    notices = (out / "codex-0.160.0.NOTICES.md").read_text()
    assert notices.count("| MPL-2.0 |") == 12


def test_ls2_the_package_archive_with_bwrap_fails_on_markers(tmp_path: Path) -> None:
    report = _codex(tmp_path, {"codex-app-server": APP_SERVER, "codex-resources/bwrap": BWRAP})
    assert report.verdict == "fail"
    assert report.markers["codex-resources/bwrap"] == {
        "GNU Lesser General Public": 1,
        "bubblewrap built for Codex": 1,
    }
    assert sorted(report.marker_failures) == [
        "codex-resources/bwrap: GNU Lesser General Public",
        "codex-resources/bwrap: bubblewrap built for Codex",
    ]


def test_ls2_markers_split_across_read_chunks_are_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scan, "CHUNK", 7)
    path = tmp_path / "blob"
    path.write_bytes(b"x" * 5 + b"GNU General Public License" + b"y" * 9)
    assert scan.scan_markers(path, ("GNU General Public License",)) == {"blob": {"GNU General Public License": 1}}


ALLOW = scan.Allowlist.load(scan.DEFAULT_ALLOWLIST, "codex")


@pytest.mark.parametrize(
    ("expression", "klass", "elected"),
    [
        ("MIT OR Apache-2.0", "permissive", "MIT"),
        ("MIT/Apache-2.0", "permissive", "MIT"),
        ("Apache-2.0 / MIT", "permissive", "Apache-2.0"),
        ("Apache-2.0 OR GPL-2.0-only", "permissive", "Apache-2.0"),
        ("(MPL-2.0 OR Apache-2.0)", "permissive", "Apache-2.0"),
        ("MPL-2.0", "notice", "MPL-2.0"),
        ("MIT AND BSD-3-Clause", "permissive", "MIT AND BSD-3-Clause"),
        ("MIT AND MPL-2.0", "notice", "MIT AND MPL-2.0"),
        ("MIT AND GPL-3.0-only", "blocked", "MIT AND GPL-3.0-only"),
        ("(MIT OR Apache-2.0) AND Unicode-3.0", "permissive", "MIT AND Unicode-3.0"),
        ("Apache-2.0 WITH LLVM-exception OR MIT", "permissive", "Apache-2.0 WITH LLVM-exception"),
        ("GPL-2.0-only WITH Classpath-exception-2.0", "blocked", "GPL-2.0-only WITH Classpath-exception-2.0"),
        ("apache-2.0", "permissive", "Apache-2.0"),
        ("MIT OR", "unknown", "MIT OR"),
        ("(MIT", "unknown", "(MIT"),
        ("UNKNOWN", "unknown", "UNKNOWN"),
    ],
)
def test_ls3_expressions(expression: str, klass: str, elected: str) -> None:
    verdict = scan.classify(expression, ALLOW)
    assert (verdict.klass, verdict.elected) == (klass, elected)


@pytest.mark.parametrize(
    ("licence", "klass"),
    [
        ("GPL-3.0-only", "blocked"),
        ("AGPL-3.0-only", "blocked"),
        ("SSPL-1.0", "blocked"),
        ("LGPL-3.0-or-later", "notice"),
        ("LGPL-2.1", "notice"),
        ("MPL-2.0", "notice"),
        ("EUPL-1.2", "blocked"),
    ],
)
def test_ls4_ls8_policy(licence: str, klass: str) -> None:
    assert scan.classify(licence, ALLOW).klass == klass


def test_ls4_a_reviewed_package_uses_the_reviewed_licence() -> None:
    allow = scan.Allowlist(ALLOW.permissive, ALLOW.weak_copyleft, {"odd@1.0.0": "MIT"})
    report = scan.Report(engine="opencode", version="1", asset="a")
    scan.apply_policy(report, [scan.Package("odd", "1.0.0", "UNKNOWN", "npm"), scan.Package("other", "1.0.0", "UNKNOWN", "npm")], allow)
    assert [b["package"] for b in report.blocked] == ["other@1.0.0"]


def test_ls5_digest_mismatch_stops_before_any_scan(tmp_path: Path) -> None:
    asset = tmp_path / "codex-app-server-x86_64-unknown-linux-musl.tar.gz"
    _archive(asset, {"codex-app-server": APP_SERVER})
    report = scan.run(
        ["codex", "0.160.0", "--asset", str(asset), "--sha256", "0" * 64, "--deps", str(CARGO_TREE), "--out", str(tmp_path)]
    )
    assert report.verdict == "fail"
    assert report.failures == ["asset sha256 does not match the pin"]
    assert report.packages == 0
    assert not (tmp_path / "codex-0.160.0.licence.json").exists()


def _opencode(tmp_path: Path, binary: bytes, *extra: str) -> object:
    asset = tmp_path / "opencode-linux-x64.tar.gz"
    digest = _archive(asset, {"opencode": binary})
    return scan.run(
        [
            "opencode", "1.18.34", "--asset", str(asset), "--sha256", digest,
            "--deps", str(FIXTURES / "opencode-mini.bun.lock"),
            "--licences", str(FIXTURES / "opencode-mini.licences.json"),
            "--out", str(tmp_path / "out"), *extra,
        ]
    )


def test_ls6_opencode_closure_elections_and_the_embedded_runtime(tmp_path: Path) -> None:
    jsc = b"\x7fELF JavaScriptCore is free software under the GNU Lesser General Public License\0"
    report = _opencode(tmp_path, jsc)
    assert report.packages == 4
    assert report.licences == {"MIT": 2, "ISC": 1, "(MPL-2.0 OR Apache-2.0)": 1}
    assert {e["package"]: e["elected"] for e in report.elections} == {"dompurify@3.3.1": "Apache-2.0"}
    assert report.marker_failures == []
    embedded = [n for n in report.notices if n["kind"] == "embedded"]
    assert [(n["licence"], n["source"]) for n in embedded] == [("LGPL-2.0-only", "https://github.com/oven-sh/WebKit")]
    assert report.verdict == "pass_with_notices"


def test_ls6_bun_closure_follows_workspaces_and_nested_resolution() -> None:
    idents = scan.bun_closure((FIXTURES / "opencode-mini.bun.lock").read_text(), "packages/opencode")
    assert idents == ["@scope/x@1.0.0", "dompurify@3.3.1", "leftpad@1.0.0", "nested@2.0.0"]


def test_ls7_a_bundle_without_a_verifier_fails_unless_allowed(tmp_path: Path) -> None:
    bundle = tmp_path / "asset.sigstore"
    bundle.write_text("{}")
    asset = tmp_path / "codex-app-server-x86_64-unknown-linux-musl.tar.gz"
    digest = _archive(asset, {"codex-app-server": APP_SERVER})
    argv = ["codex", "0.160.0", "--asset", str(asset), "--sha256", digest, "--deps", str(CARGO_TREE),
            "--out", str(tmp_path / "out"), "--signature-bundle", str(bundle)]
    refused = scan.run(argv, which=lambda _name: None)
    assert (refused.signature, refused.verdict) == ("verifier_missing", "fail")
    assert refused.failures == ["signature verifier_missing"]
    allowed = scan.run([*argv, "--allow-unverified-signature"], which=lambda _name: None)
    assert (allowed.signature, allowed.verdict) == ("verifier_missing", "pass_with_notices")


def test_ls7_cosign_must_see_the_engine_release_workflow_as_signer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """X6 legal, found while building: a wildcard identity accepted any Fulcio certificate as the engine's signature."""
    asset = tmp_path / "a.tar.gz"
    _archive(asset, {"codex-app-server": APP_SERVER})
    bundle = tmp_path / "a.sigstore"
    bundle.write_text("{}")
    commands: list[list[str]] = []

    def refuse(command: list[str], **_kwargs: object) -> object:
        commands.append(command)
        return type("Done", (), {"returncode": 1})()

    monkeypatch.setattr(scan.subprocess, "run", refuse)
    status = scan.verify_signature(asset, bundle, "openai/codex", which=lambda name: f"/bin/{name}" if name == "cosign" else None)
    assert status == "failed"
    first = commands[0]
    assert first[first.index("--certificate-identity-regexp") + 1] == "^https://github\\.com/openai/codex/"
    assert first[first.index("--certificate-oidc-issuer") + 1] == "https://token.actions.githubusercontent.com"
    assert ".*" not in first


def test_ls8_a_new_licence_blocks_the_upgrade(tmp_path: Path) -> None:
    tree = tmp_path / "tree.txt"
    tree.write_text(CARGO_TREE.read_text() + "newcrate v1.0.0|EUPL-1.2\n")
    asset = tmp_path / "codex-app-server-x86_64-unknown-linux-musl.tar.gz"
    digest = _archive(asset, {"codex-app-server": APP_SERVER})
    report = scan.run(["codex", "0.160.0", "--asset", str(asset), "--sha256", digest, "--deps", str(tree), "--out", str(tmp_path)])
    assert report.verdict == "fail"
    assert report.blocked == [{"package": "newcrate@1.0.0", "licence": "EUPL-1.2", "reason": "blocked"}]


def test_main_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    asset = tmp_path / "codex-app-server-x86_64-unknown-linux-musl.tar.gz"
    digest = _archive(asset, {"codex-app-server": APP_SERVER})
    base = ["codex", "0.160.0", "--asset", str(asset), "--deps", str(CARGO_TREE), "--out", str(tmp_path)]
    assert scan.main([*base, "--sha256", digest]) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "pass_with_notices"
    assert scan.main([*base, "--sha256", "1" * 64]) == 1


def test_cargo_lock_parsing_keeps_registry_packages_only() -> None:
    lock = (
        '[[package]]\nname = "a"\nversion = "1.0.0"\nsource = "registry+https://github.com/rust-lang/crates.io-index"\n\n'
        '[[package]]\nname = "local"\nversion = "0.1.0"\n\n'
        '[[package]]\nname = "b"\nversion = "2.0.0"\nsource = "registry+https://github.com/rust-lang/crates.io-index"\n'
    )
    assert scan.parse_cargo_lock(lock) == [("a", "1.0.0"), ("b", "2.0.0")]


def test_bundle_names_cover_the_codex_naming() -> None:
    names = scan.bundle_names("codex-app-server-x86_64-unknown-linux-musl.tar.gz")
    assert "codex-app-server-x86_64-unknown-linux-musl.sigstore" in names
    assert "codex-app-server-x86_64-unknown-linux-musl.tar.gz.sigstore" in names


def _layer(
    tmp_path: Path, members: dict[str, bytes], components: list[dict[str, object]], engine: str = "opencode"
) -> object:
    layer = tmp_path / "layer.tar"
    digest = _archive(layer, members)
    manifest = tmp_path / "components.json"
    manifest.write_text(json.dumps({"components": components}))
    return scan.run(
        [engine, "1.18.34-tools", "--asset", str(layer), "--sha256", digest, "--manifest", str(manifest),
         "--out", str(tmp_path / "out")]
    )


PYPI_OK: list[dict[str, object]] = [
    {"name": "pandas", "version": "3.0.6", "licence": "BSD-3-Clause", "ecosystem": "pypi"},
    {"name": "packaging", "version": "26.3", "licence": "Apache-2.0 OR BSD-2-Clause", "ecosystem": "pypi"},
    {"name": "libquadmath", "version": "numpy.libs", "licence": "LGPL-2.1-or-later", "ecosystem": "native",
     "source": "https://gcc.gnu.org/", "markers": ["GNU Lesser General Public"]},
]


def test_ls9_a_layer_manifest_replaces_the_lockfile_closure(tmp_path: Path) -> None:
    """E0b LS-9: components with their licences; PyPI sources by default; LGPL is a notice; no engine notices."""
    report = _layer(tmp_path, {"site-packages/pandas/__init__.py": b"# BSD"}, PYPI_OK)
    assert report.packages == 3
    assert report.dependency_source == "layer manifest (components.json)"
    assert {e["package"]: e["elected"] for e in report.elections} == {"packaging@26.3": "Apache-2.0"}
    assert [(n["package"], n["licence"], n["source"]) for n in report.notices] == [
        ("libquadmath@numpy.libs", "LGPL-2.1-or-later", "https://gcc.gnu.org/")
    ]
    assert report.verdict == "pass_with_notices"


@pytest.mark.parametrize(
    ("licence", "reason"),
    [("GPL-3.0-or-later WITH GCC-exception-3.1", "blocked"), ("UNKNOWN", "unknown"), ("LicenseRef-Matplotlib", "blocked")],
)
def test_ls9_a_component_outside_the_policy_fails_the_layer(tmp_path: Path, licence: str, reason: str) -> None:
    report = _layer(tmp_path, {"x": b"x"}, [*PYPI_OK, {"name": "c", "version": "1", "licence": licence, "ecosystem": "native"}])
    assert report.blocked == [{"package": "c@1", "licence": licence, "reason": reason}]
    assert report.verdict == "fail"


def test_ls10_markers_in_a_layer_follow_the_same_rules(tmp_path: Path) -> None:
    """E0b LS-10: a GPL text no component declares fails; a declared LGPL text does not."""
    members = {
        "numpy.libs/libquadmath.so": b"\x7fELF GNU Lesser General Public License",
        "numpy/LICENSE.txt": b"GNU GENERAL PUBLIC LICENSE Version 3",
    }
    report = _layer(tmp_path, members, PYPI_OK)
    assert report.marker_failures == ["numpy/LICENSE.txt: GNU GENERAL PUBLIC LICENSE"]
    assert report.verdict == "fail"
    declared = [*PYPI_OK, {"name": "libgfortran", "version": "numpy.libs", "licence": "LGPL-2.1-or-later",
                           "ecosystem": "native", "markers": ["GNU GENERAL PUBLIC LICENSE"]}]
    assert _layer(tmp_path, members, declared).marker_failures == []


# LS-11..LS-17 (X6 legal): per-component, per-version legal approvals in the allowlist.

APPROVAL = scan.Approval("maintainers, for legal (D29)", "2026-10-04", "notes/10 §4c item 4")
GCC = "GPL-3.0-or-later WITH GCC-exception-3.1"
TOOLS_MANIFEST = REPO / "deployment" / "sandbox-images" / "opencode" / "tools" / "licenses" / "components.json"
POLICY = json.loads(scan.DEFAULT_ALLOWLIST.read_text())


def _approved(reviewed: dict[str, object]) -> object:
    return scan.Allowlist(ALLOW.permissive, ALLOW.weak_copyleft, reviewed)


def _judge(allow: object, *packages: object) -> object:
    report = scan.Report(engine="opencode", version="1", asset="a")
    scan.apply_policy(report, packages, allow)
    return report.finish()


def test_ls11_an_approved_component_at_its_version_passes_with_a_cited_notice() -> None:
    allow = _approved({"gdbm@1.26-r0": scan.Approved("GPL-3.0-or-later", APPROVAL)})
    report = _judge(allow, scan.Package("gdbm", "1.26-r0", "GPL-3.0-or-later", "apk", "https://example/gdbm"))
    assert report.blocked == []
    assert report.notices == [
        {"package": "gdbm@1.26-r0", "licence": "GPL-3.0-or-later", "source": "https://example/gdbm", "kind": "dependency",
         "approval": "maintainers, for legal (D29), 2026-10-04 (notes/10 §4c item 4)"}
    ]
    assert report.verdict == "pass_with_notices"


def test_ls12_the_same_component_at_another_version_fails() -> None:
    allow = _approved({"gdbm@1.26-r0": scan.Approved("GPL-3.0-or-later", APPROVAL)})
    report = _judge(allow, scan.Package("gdbm", "1.27-r0", "GPL-3.0-or-later", "apk"))
    assert report.blocked == [{"package": "gdbm@1.27-r0", "licence": "GPL-3.0-or-later", "reason": "blocked"}]
    assert report.verdict == "fail"


def test_ls13_an_unreviewed_gpl_component_still_fails_beside_an_approved_one() -> None:
    allow = _approved({"gdbm@1.26-r0": scan.Approved("GPL-3.0-or-later", APPROVAL)})
    report = _judge(
        allow,
        scan.Package("gdbm", "1.26-r0", "GPL-3.0-or-later", "apk"),
        scan.Package("readline", "8.3-r0", "GPL-3.0-or-later", "apk"),
    )
    assert report.blocked == [{"package": "readline@8.3-r0", "licence": "GPL-3.0-or-later", "reason": "blocked"}]
    assert report.verdict == "fail"
    assert scan.classify("GPL-3.0-or-later", allow).klass == "blocked"


def test_ls14_an_approval_holds_only_for_the_licence_it_was_given_for() -> None:
    allow = _approved({"poe-oauth@0.0.8": scan.Approved("UNKNOWN", APPROVAL)})
    report = _judge(allow, scan.Package("poe-oauth", "0.0.8", "AGPL-3.0-only", "npm"))
    assert report.blocked == [{"package": "poe-oauth@0.0.8", "licence": "AGPL-3.0-only", "reason": "blocked"}]


def test_ls15_a_bundled_library_fails_once_its_carrier_moves() -> None:
    allow = _approved({"libgfortran@numpy.libs": scan.Approved(GCC, APPROVAL, ("numpy@2.5.3",))})
    lib = scan.Package("libgfortran", "numpy.libs", GCC, "native")
    assert _judge(allow, lib, scan.Package("numpy", "2.5.3", "BSD-3-Clause", "pypi")).verdict == "pass_with_notices"
    moved = _judge(allow, lib, scan.Package("numpy", "2.6.0", "BSD-3-Clause", "pypi"))
    assert moved.blocked == [{"package": "libgfortran@numpy.libs", "licence": GCC, "reason": "blocked"}]


def _with_markers(tmp_path: Path, markers: dict[str, list[dict[str, object]]]) -> str:
    allowlist = tmp_path / "allow.json"
    allowlist.write_text(json.dumps({**POLICY, "reviewed": {}, "reviewed_markers": {"opencode": markers}}))
    return str(allowlist)


def test_ls16_an_approved_marker_holds_for_its_member_and_engine_version_only(tmp_path: Path) -> None:
    gpl = b"\x7fELF libtcc1: GNU General Public License, with the runtime exception\0"
    marker: dict[str, object] = {
        "member": "opencode", "marker": "GNU General Public License", "component": "tinycc libtcc1",
        "licence": "GPL-2.0-or-later WITH GCC-exception-2.0", "source": "https://github.com/oven-sh/tinycc",
        "approval": {"by": "legal", "date": "2026-10-04", "ref": "notes/10 §4c item 4"},
    }
    report = _opencode(tmp_path, gpl, "--allowlist", _with_markers(tmp_path, {"1.18.34": [marker]}))
    assert report.marker_failures == []
    assert ("tinycc libtcc1", "GPL-2.0-or-later WITH GCC-exception-2.0") in {(n["package"], n["licence"]) for n in report.notices}
    assert report.verdict == "pass_with_notices"
    other_version = _opencode(tmp_path, gpl, "--allowlist", _with_markers(tmp_path, {"1.18.33": [marker]}))
    assert other_version.marker_failures == ["opencode: GNU General Public License"]
    other_member = _opencode(tmp_path, gpl, "--allowlist", _with_markers(tmp_path, {"1.18.34": [{**marker, "member": "bun"}]}))
    assert other_member.verdict == "fail"


def test_ls16_an_approval_without_who_when_and_where_is_refused(tmp_path: Path) -> None:
    allowlist = tmp_path / "allow.json"
    entry = {"licence": "UNKNOWN", "approval": {"by": "legal"}}
    allowlist.write_text(json.dumps({**POLICY, "reviewed": {"opencode": {"x@1": entry}}}))
    with pytest.raises(ValueError, match="by, date and ref"):
        scan.Allowlist.load(allowlist, "opencode")


SCIPY_GCC = {"libgcc_s@scipy.libs", "libstdc++@scipy.libs", "libgfortran@scipy.libs"}
PDF_PACKS = {
    "libgcc_s@cryptography.libs", "libgcc_s@pypdfium2.libs", "libstdc++@pypdfium2.libs",
    "freetype@pypdfium2 (static)", "libjpeg-turbo@pypdfium2 (static)", "libpng16@pypdfium2 (static)",
    "libtiff@pypdfium2 (static)", "agg@pypdfium2 (static)", "bitstream-vera@reportlab", "adobe-core14-afm@pdfkit",
}


def test_ls17_the_policy_records_the_2026_10_04_approval_without_widening_the_lists() -> None:
    listed = POLICY["permissive"] + POLICY["weak_copyleft_with_notice"]
    assert [x for x in listed if x.startswith(("GPL", "AGPL"))] == []
    entries = [e for engine in POLICY["reviewed"].values() for e in engine.values()]
    entries += list(POLICY["reviewed_shared"].values())
    entries += [m for versions in POLICY["reviewed_markers"].values() for ms in versions.values() for m in ms]
    assert len(entries) == 12 + 2 + 22 + 1 + 4 + 10 + 1
    first = [e for e in entries if e["approval"]["date"] == "2026-10-04"]
    assert len(first) == 12 + 2 + 22 + 1 and all(e["approval"]["ref"] == "D29 legal review, item 4c.4" for e in first)
    # SKILLPACKS: the 2026-10-30 approvals cover exactly what scipy, the Node office libraries and the pdf and pptx
    # packs' libraries bring that the lists do not pass.
    # WP-78: the recorded 2026-11-03 approval of the Goose licences is the one later engine entry (LS-21).
    assert [k for engine in POLICY["reviewed"].values() for k, e in engine.items() if e["approval"]["date"] == "2026-11-03"] == [
        "ansi_colours@1.2.3"
    ]
    later = {k: e for k, e in POLICY["reviewed_shared"].items() if e["approval"]["date"] == "2026-10-30"}
    assert set(later) == SCIPY_GCC | {"buffers@0.1.1"} | PDF_PACKS
    assert all("SKILLPACKS" in e["approval"]["ref"] for e in later.values())
    assert all(e.get("bundled_in") == ["scipy@1.18.1"] for k, e in later.items() if k.endswith("scipy.libs"))
    assert all(e.get("bundled_in") == ["pypdfium2@5.14.0"] for k, e in later.items() if "pypdfium2" in k)


def test_ls18_a_shared_approval_holds_in_every_engines_scan_and_an_engines_own_entry_wins(tmp_path: Path) -> None:
    """SKILLPACKS: the runtime layer is one component set in every engine image, so its approvals are too."""
    for engine in ("opencode", "codex", "claude_code", "acp-gemini"):
        allow = scan.Allowlist.load(scan.DEFAULT_ALLOWLIST, engine)
        assert {"matplotlib@3.11.2", "libgfortran@numpy.libs", "buffers@0.1.1"} <= set(allow.reviewed), engine
    assert "poe-oauth@0.0.8" not in scan.Allowlist.load(scan.DEFAULT_ALLOWLIST, "codex").reviewed
    policy = tmp_path / "policy.json"
    own = {"licence": "GPL-2.0-only", "approval": {"by": "x", "date": "2026-10-30", "ref": "y"}}
    shared = {"licence": "MIT-CMU", "approval": {"by": "x", "date": "2026-10-30", "ref": "y"}}
    policy.write_text(json.dumps({
        "permissive": ["MIT"], "weak_copyleft_with_notice": [],
        "reviewed": {"codex": {"pillow@12.3.0": own}}, "reviewed_shared": {"pillow@12.3.0": shared},
    }))
    assert scan.Allowlist.load(policy, "codex").reviewed["pillow@12.3.0"].licence == "GPL-2.0-only"
    assert scan.Allowlist.load(policy, "opencode").reviewed["pillow@12.3.0"].licence == "MIT-CMU"
    components = json.loads(TOOLS_MANIFEST.read_text())["components"]
    for engine in ("codex", "claude_code", "acp-gemini"):
        report = _layer(tmp_path, {"x": b"x"}, components, engine)
        assert (report.verdict, report.blocked) == ("pass_with_notices", []), engine


def test_ls18_an_npm_bundle_engine_is_scanned_only_as_an_image_layer(tmp_path: Path) -> None:
    asset = tmp_path / "a.tar.gz"
    digest = _archive(asset, {"x": b"x"})
    with pytest.raises(SystemExit, match="only an image layer with --manifest"):
        scan.run(["acp-gemini", "0.63.0", "--asset", str(asset), "--sha256", digest, "--out", str(tmp_path)])


def test_ls17_the_tools_layer_manifest_passes_and_an_upgrade_does_not(tmp_path: Path) -> None:
    components = json.loads(TOOLS_MANIFEST.read_text())["components"]
    report = _layer(tmp_path, {"x": b"x"}, components)
    assert (report.verdict, report.blocked, report.packages) == ("pass_with_notices", [], 320)
    assert sum(1 for n in report.notices if "approval" in n) == 22 + 4 + 10
    bumped = [{**c, "version": "12.4.0"} if c["name"] == "pillow" else c for c in components]
    blocked = {b["package"] for b in _layer(tmp_path, {"x": b"x"}, bumped).blocked}
    assert "pillow@12.4.0" in blocked
    assert {"libtiff@pillow.libs", "libbsd@pillow.libs", "freetype@pillow.libs"} <= blocked
    rescipied = [{**c, "version": "1.19.0"} if c["name"] == "scipy" else c for c in components]
    blocked = {b["package"] for b in _layer(tmp_path, {"x": b"x"}, rescipied).blocked}
    assert blocked == SCIPY_GCC
    repdfed = [{**c, "version": "5.15.0"} if c["name"] == "pypdfium2" else c for c in components]
    blocked = {b["package"] for b in _layer(tmp_path, {"x": b"x"}, repdfed).blocked}
    assert blocked == {k for k in PDF_PACKS if "pypdfium2" in k}


def _debian_manifest() -> ModuleType:
    spec = importlib.util.spec_from_file_location("debian_manifest", SCRIPT.parent / "debian_manifest.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("debian", "spdx"),
    [
        ("Expat", "MIT"), ("BSD3", "BSD-3-Clause"), ("GPL-2+", "GPL-2.0-or-later"), ("GPL-3+", "GPL-3.0-or-later"),
        ("Artistic or GPL-1+", "Artistic-1.0-Perl OR GPL-1.0-or-later"), ("public-domain", "Public-Domain"),
        ("OpenLDAP-2.8", "OLDAP-2.8"), ("", "UNKNOWN"), ("Some-Other", "Some-Other"),
    ],
)
def test_ls19_debian_licence_names_become_spdx_ids_only_where_the_meaning_is_one(debian: str, spdx: str) -> None:
    assert _debian_manifest().spdx(debian) == spdx


def test_ls19_the_claude_code_layer_manifest_carries_the_shared_runtime_layer_and_node() -> None:
    """claude-code/scan.sh: Debian packages, the relay, the tools layer except Alpine's packages, the glibc Node."""
    components = json.loads(TOOLS_MANIFEST.read_text())["components"]
    rows = _debian_manifest().manifest("git\t1:2.39.5\nripgrep\t13.0.0\n", "git\tGPL-2\nripgrep\tExpat\n", components, "24.22.1")
    by_ident = {f"{c['name']}@{c['version']}": c for c in rows}
    assert by_ident["git@1:2.39.5"]["licence"] == "GPL-2.0-only" and by_ident["ripgrep@13.0.0"]["licence"] == "MIT"
    assert by_ident["nodejs@24.22.1"]["ecosystem"] == "runtime" and "pxh-relay@1" in by_ident
    assert not any(c["ecosystem"] == "apk" for c in rows)
    assert {"scipy@1.18.1", "pdfplumber@0.11.10", "exceljs@4.4.0", "pptxgenjs@4.0.1"} <= set(by_ident)


def test_ls20_every_allowlisted_engine_kind_can_be_scanned() -> None:
    """Round 16 (WP-76 Goose): an engine shipped in allowlist.json is one the scanner knows, binary and tools layer."""
    allowlist = json.loads((REPO / "backend/python/harness_engines/allowlist.json").read_text(encoding="utf-8"))
    # A kind that runs another kind's image (acp-opencode: opencode's binary and image) is that kind's scan.
    scanned_images = {entry["image"] for entry in allowlist["engines"] if entry["kind"] in scan.ENGINES}
    unscanned = {e["kind"] for e in allowlist["engines"] if e["kind"] not in scan.ENGINES and e["image"] not in scanned_images}
    assert unscanned == set()
    goose = scan.ENGINES["acp-goose"]
    assert (goose.repo, goose.tag_for("1.57.0"), goose.asset_for("1.57.0", "x86_64"), goose.ecosystem) == (
        "aaif-goose/goose",
        "v1.57.0",
        "goose-x86_64-unknown-linux-musl.tar.gz",
        "cargo",
    )


# LS-21 (WP-78): Goose's release binary is a static Rust
# build, so an LGPL crate in it is relinkable only through the image's notice and source pointer.

GOOSE_CRATES = FIXTURES / "goose-v1.57.0.cargo-lock-licences.txt"
GOOSE_IMAGE = REPO / "deployment" / "sandbox-images" / "acp-goose"


def _goose(tmp_path: Path, deps: str | None = None, *extra: str) -> object:
    asset = tmp_path / "goose-x86_64-unknown-linux-musl.tar.gz"
    digest = _archive(asset, {"goose": APP_SERVER})
    lock = tmp_path / "crates.txt"
    lock.write_text(GOOSE_CRATES.read_text() + (deps or ""))
    return scan.run(
        ["acp-goose", "1.57.0", "--asset", str(asset), "--sha256", digest, "--deps", str(lock), "--out", str(tmp_path / "out"), *extra]
    )


def test_ls21_the_goose_binary_passes_with_its_recorded_approvals(tmp_path: Path) -> None:
    report = _goose(tmp_path)
    assert (report.verdict, report.blocked, report.packages) == ("pass_with_notices", [], 1112)
    lgpl = [n for n in report.notices if "LGPL" in n["licence"]]
    assert lgpl == [
        {"package": "ansi_colours@1.2.3", "licence": "LGPL-3.0-or-later", "source": "https://crates.io/crates/ansi_colours/1.2.3",
         "kind": "dependency", "approval": lgpl[0]["approval"]}
    ]
    assert lgpl[0]["approval"].startswith("maintainers") and "2026-11-03" in lgpl[0]["approval"]
    # The other 23 notices are MPL-2.0 (file-level copyleft: no relinking duty), the round-16 scan's.
    assert sorted(n["licence"] for n in report.notices if n is not lgpl[0]) == ["MPL-2.0"] * 23
    assert "libbz2-rs-sys@0.2.5" not in {n["package"] for n in report.notices}
    notices = (tmp_path / "out" / "acp-goose-1.57.0.NOTICES.md").read_text()
    assert "| ansi_colours@1.2.3 | LGPL-3.0-or-later |" in notices


def test_ls21_bzip2_1_0_6_is_a_permissive_licence_for_every_engine() -> None:
    """bzip2-1.0.6 is BSD-style (keep the notice in source, mark altered versions, no endorsement): no copyleft."""
    for engine in scan.ENGINES:
        allow = scan.Allowlist.load(scan.DEFAULT_ALLOWLIST, engine)
        assert scan.classify("bzip2-1.0.6", allow).klass == "permissive", engine


@pytest.mark.parametrize(
    ("line", "ident"),
    [
        ("termcolour v0.1.0|LGPL-2.1-or-later\n", "termcolour@0.1.0"),
        ("palette-lgpl v2.0.0|LGPL-3.0-only AND MIT\n", "palette-lgpl@2.0.0"),
    ],
)
def test_ls21_an_unapproved_lgpl_crate_in_a_static_binary_still_fails(tmp_path: Path, line: str, ident: str) -> None:
    report = _goose(tmp_path, line)
    assert report.blocked == [{"package": ident, "licence": line.split("|")[1].strip(), "reason": "static_lgpl_unapproved"}]
    assert report.verdict == "fail"


def test_ls21_the_approval_holds_only_for_the_version_it_names(tmp_path: Path) -> None:
    moved = GOOSE_CRATES.read_text().replace("ansi_colours v1.2.3|", "ansi_colours v1.2.4|")
    lock = tmp_path / "moved.txt"
    lock.write_text(moved)
    asset = tmp_path / "goose.tar.gz"
    digest = _archive(asset, {"goose": APP_SERVER})
    report = scan.run(["acp-goose", "1.58.0", "--asset", str(asset), "--sha256", digest, "--deps", str(lock), "--out", str(tmp_path)])
    assert report.blocked == [{"package": "ansi_colours@1.2.4", "licence": "LGPL-3.0-or-later", "reason": "static_lgpl_unapproved"}]


def test_ls21_lgpl_outside_a_static_binary_stays_a_notice() -> None:
    """D29 unchanged for image layers: a shared LGPL library is replaceable in place, so it needs no approval."""
    allow = scan.Allowlist.load(scan.DEFAULT_ALLOWLIST, "acp-goose")
    report = _judge(allow, scan.Package("libquadmath", "numpy.libs", "LGPL-2.1-or-later", "native"))
    assert (report.verdict, report.blocked) == ("pass_with_notices", [])


def _image_dir(engine: str) -> Path:
    return REPO / "deployment" / "sandbox-images" / engine.replace("_", "-")


@pytest.mark.parametrize("engine", sorted(POLICY["reviewed"]))
def test_ls21_every_approved_static_lgpl_crate_is_named_in_its_images_notice(engine: str) -> None:
    approved = [k for k, e in POLICY["reviewed"][engine].items() if "LGPL" in e["licence"]]
    if scan.ENGINES[engine].ecosystem != "cargo" or not approved:
        pytest.skip(f"{engine}: no approved LGPL crate in a static binary")
    notice = (_image_dir(engine) / "licenses" / "NOTICE.md").read_text(encoding="utf-8")
    for ident in approved:
        name, version = ident.split("@")
        assert f"{name} {version}" in notice, ident


def test_ls21_the_goose_image_ships_the_lgpl_texts_the_notice_and_the_source_pointer() -> None:
    """LGPL-3.0 §4 for a static link: both licence texts, the library's notice, and the means to relink."""
    licenses = GOOSE_IMAGE / "licenses"
    assert (licenses / "LGPL-3.0.txt").read_text(encoding="utf-8").lstrip().startswith("GNU LESSER GENERAL PUBLIC LICENSE\n")
    assert (licenses / "GPL-3.0.txt").read_text(encoding="utf-8").lstrip().startswith("GNU GENERAL PUBLIC LICENSE\n")
    notice = (licenses / "NOTICE.md").read_text(encoding="utf-8")
    assert "ansi_colours 1.2.3" in notice and "LGPL-3.0-or-later" in notice
    assert "Copyright 2018 by Michał Nazarewicz" in notice
    assert "https://github.com/aaif-goose/goose/tree/v1.57.0" in notice and "Cargo.lock" in notice
    assert "https://crates.io/crates/ansi_colours/1.2.3" in notice
    assert "written offer" in notice.lower()
    assert "not yet scanned" not in notice
    dockerfile = (GOOSE_IMAGE / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY licenses/ /usr/share/licenses/pxh-goose/" in dockerfile


# LS-22 (C2.43): a Debian engine image is scanned by debian-layer-scan.sh, an Alpine one by opencode/tools/scan.sh.

IMAGES = REPO / "deployment" / "sandbox-images"
DEBIAN_SCAN = IMAGES / "debian-layer-scan.sh"


def _debian_kinds() -> dict[str, str]:
    """debian-layer-scan.sh's case table: kind -> the engine path it leaves to the engine's own scan."""
    rows = re.findall(r"^\s+([\w-]+)\) DIR=([\w-]+) NOTICES=[\w-]+ EXCLUDE=(\S+) ;;$", DEBIAN_SCAN.read_text(), re.M)
    return {kind: f"{folder}:{exclude}" for kind, folder, exclude in rows}


def test_ls22_every_debian_engine_image_has_a_layer_scan_that_leaves_out_only_the_engine() -> None:
    allowlist = json.loads((REPO / "backend/python/harness_engines/allowlist.json").read_text(encoding="utf-8"))
    debian = {
        e["kind"]: folder
        for e in allowlist["engines"]
        for folder in [e["kind"].replace("_", "-")]
        if (IMAGES / folder / "Dockerfile").is_file()
        and re.search(r"^ARG DEBIAN=", (IMAGES / folder / "Dockerfile").read_text(), re.M)
    }
    kinds = _debian_kinds()
    assert set(kinds) == set(debian) == {"claude_code", "acp-dsh"}
    for kind, row in kinds.items():
        folder, exclude = row.split(":", 1)
        assert folder == debian[kind]
        # The excluded path is where the image puts the engine, and the folder's scan.sh runs this kind.
        assert f"/{exclude}" in (IMAGES / folder / "Dockerfile").read_text()
        assert f"debian-layer-scan.sh\" {kind} " in (IMAGES / folder / "scan.sh").read_text()
        # The manifest's tools components are the Alpine tools layer's own: one copy, never a stale fork.
        assert (IMAGES / folder / "tools").resolve() == (IMAGES / "opencode" / "tools").resolve()


def test_ls22_the_debian_scan_refuses_a_kind_it_does_not_know() -> None:
    import subprocess

    done = subprocess.run(["sh", str(DEBIAN_SCAN), "opencode"], capture_output=True, text=True, check=False, timeout=30)
    assert done.returncode == 2 and "not a Debian engine image" in done.stderr
