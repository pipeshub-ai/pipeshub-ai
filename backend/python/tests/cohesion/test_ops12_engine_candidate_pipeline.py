"""OPS-12 (C14.03, ADR-OPS-01 D7): the engine upgrade pipeline's candidate stage.

A scheduled job resolves each allow-listed engine's upstream `latest`, skips a version already allow-listed, builds
a candidate with CC1's path, runs CC1's gate (PXH_ENGINE_CANDIDATE) and on a pass uploads a candidate catalog entry
from scripts/engine_candidate_catalog.py, never touching the allowlist. These hold the entry's schema, the skip rule
and the gate's lanes.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import sys
import tarfile
import zipfile
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from tests.cohesion.support import PYTHON_ROOT, REPO_ROOT

if TYPE_CHECKING:
    from pathlib import Path

sys.path.insert(0, str(REPO_ROOT / "scripts"))

import engine_candidate_catalog as cat

ALLOWLIST = PYTHON_ROOT / "harness_engines" / "allowlist.json"
RUN_URL = "https://github.com/pipeshub-ai/pipeshub-ai/actions/runs/123456"
D_BIN = "sha256:" + "b" * 64
D_IMG = "sha256:" + "c" * 64
ASSET = "d" * 64


@pytest.fixture
def allowlist() -> dict[str, Any]:
    return cat.load_allowlist()


def _passed(kind: str) -> dict[str, str]:
    return {lane["name"]: "pass" for lane in cat.lanes(kind)}


def _entry(allowlist: dict[str, Any], kind: str = "opencode", version: str = "9.9.9",
           over: dict[str, Any] | None = None) -> dict[str, Any]:
    src = cat.source(allowlist, kind)
    entry = cat.make_entry(
        allowlist, kind=kind, version=version, image=cat.candidate_image(src["image"], version), image_digest=D_IMG,
        binary_digest=D_BIN, asset_sha256=None if src["build"] == "admin" else ASSET, lane_results=_passed(kind),
        run_url=RUN_URL, now=datetime(2026, 11, 5, tzinfo=UTC),
    )
    entry.update(over or {})
    return entry


# --- the catalog entry ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["opencode", "codex", "claude_code", "acp-opencode", "acp-gemini", "acp-goose", "acp-qwen", "acp-dsh"])
def test_ops12_a_passed_candidates_entry_validates(allowlist: dict[str, Any], kind: str) -> None:
    entry = _entry(allowlist, kind)
    assert cat.validate(entry, allowlist) == []
    assert entry["previous"] == cat.default_entry(allowlist, kind)["version"]
    assert entry["image"].endswith(":9.9.9-candidate")
    assert json.loads(json.dumps(entry)) == entry


BAD = [
    ("unknown kind", {"kind": "vim"}, "not allow-listed"),
    ("already listed", {"version": "1.18.34", "image": "pxh-opencode-tools:1.18.34-candidate"}, "already allow-listed"),
    ("version not tag-safe", {"version": "9.9.9 rc"}, "tag-safe"),
    ("image not the candidate tag", {"image": "pxh-opencode-tools:9.9.9"}, "candidate tag"),
    ("short digest", {"binaryDigest": "sha256:abc"}, "binaryDigest"),
    ("upper-case image digest", {"imageDigest": "sha256:" + "C" * 64}, "imageDigest"),
    ("release engine without asset sha", {"assetSha256": None}, "assetSha256"),
    ("wrong previous", {"previous": "1.0.0"}, "previous"),
    ("failed gate", {"gate": {"result": "fail", "lanes": {**_passed("opencode"), "conformance": "fail"}}},
     "only for a candidate whose gate passed"),
    ("skipped lane said pass", {"gate": {"result": "pass", "lanes": {**_passed("opencode"), "tool-matrix": "skipped"}}},
     "exactly when every lane passed"),
    ("missing lane", {"gate": {"result": "pass", "lanes": {"conformance": "pass"}}}, "gate.lanes"),
    ("not a run url", {"runUrl": "https://example.com/run/1"}, "runUrl"),
    ("bad time", {"createdAt": "yesterday"}, "createdAt"),
    ("schema", {"schemaVersion": 2}, "schemaVersion"),
]


@pytest.mark.parametrize(("case", "over", "reason"), BAD, ids=[b[0] for b in BAD])
def test_ops12_a_bad_entry_is_refused_naming_why(allowlist: dict[str, Any], case: str, over: dict, reason: str) -> None:
    entry = _entry(allowlist, "opencode", over=over)
    errors = cat.validate(entry, allowlist)
    assert any(reason in e for e in errors), (case, errors)


def test_ops12_entry_shape_is_closed(allowlist: dict[str, Any]) -> None:
    entry = _entry(allowlist)
    extra = {**entry, "pushed": True}
    assert any("unknown: pushed" in e for e in cat.validate(extra, allowlist))
    missing = {k: v for k, v in entry.items() if k != "runUrl"}
    assert any("missing: runUrl" in e for e in cat.validate(missing, allowlist))
    assert cat.validate([], allowlist) == ["an entry is a JSON object"]
    admin = _entry(allowlist, "claude_code", over={"assetSha256": ASSET})
    assert any("null for an admin-supplied" in e for e in cat.validate(admin, allowlist))


def test_ops12_entry_refuses_to_write_a_failed_gate(allowlist: dict[str, Any], tmp_path: Path) -> None:
    lanes = tmp_path / "lanes.json"
    lanes.write_text(json.dumps({**_passed("codex"), "controller": "fail"}))
    out = tmp_path / "entry.json"
    argv = ["entry", "--kind", "codex", "--version", "9.9.9", "--image", "pxh-codex-tools:9.9.9-candidate",
            "--image-digest", D_IMG, "--binary-digest", D_BIN, "--asset-sha256", ASSET, "--lanes", str(lanes),
            "--run-url", RUN_URL, "--out", str(out)]
    assert cat.main(argv, out=io.StringIO()) == 1
    assert not out.exists()
    lanes.write_text(json.dumps(_passed("codex")))
    assert cat.main(argv, out=io.StringIO()) == 0
    assert cat.main(["validate", str(out)], out=io.StringIO()) == 0


# --- the skip rule -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["opencode", "codex", "claude_code", "acp-opencode", "acp-gemini", "acp-goose", "acp-qwen", "acp-dsh"])
def test_ops12_latest_equal_to_the_allowlisted_version_is_skipped(allowlist: dict[str, Any], kind: str) -> None:
    src = cat.source(allowlist, kind)
    assert cat.decide(allowlist, kind, src["tagPrefix"] + src["allowlisted"])["action"] == "skip"
    newer = cat.decide(allowlist, kind, src["tagPrefix"] + "999.0.0")
    assert (newer["action"], newer["version"]) == ("candidate", "999.0.0")


def test_ops12_a_version_listed_beside_the_default_is_skipped_too(allowlist: dict[str, Any]) -> None:
    two = copy.deepcopy(allowlist)
    extra = copy.deepcopy(cat.default_entry(two, "codex"))
    extra["version"] = "0.170.0"
    two["engines"].append(extra)
    assert cat.decide(two, "codex", "rust-v0.170.0")["action"] == "skip"


def test_ops12_a_tag_off_the_release_line_is_unresolved(allowlist: dict[str, Any]) -> None:
    assert cat.decide(allowlist, "codex", "v0.161.0")["action"] == "unresolved"
    assert cat.decide(allowlist, "opencode", "v")["action"] == "unresolved"
    assert cat.decide(allowlist, "opencode", "v1.0 beta")["action"] == "unresolved"


# --- sources, assets and lanes -------------------------------------------------------------------------------------


def test_ops12_sources_cover_every_allowlisted_kind_with_a_way_to_resolve_it(allowlist: dict[str, Any]) -> None:
    found = {s["kind"]: s for s in cat.sources(allowlist)}
    assert list(found) == list(dict.fromkeys(e["kind"] for e in allowlist["engines"]))
    assert set(found) == set(cat.GATE_LANES)
    for kind, src in found.items():
        entry = cat.default_entry(allowlist, kind)
        if entry.get("supply") == "admin":
            assert (src["resolve"], src["build"]) == ("npm", "admin")
        else:
            assert (src["resolve"] == "github-release" and src["repo"].count("/") == 1) or (
                src["resolve"] == "npm" and src["package"] and src["build"] == "release"
            ), kind
            build_dir = REPO_ROOT / "deployment" / "sandbox-images" / src["buildKind"].replace("_", "-")
            assert (build_dir / "build.sh").exists(), kind
    assert found["acp-opencode"]["build"] == "follows" and found["acp-opencode"]["buildKind"] == "opencode"


def test_ops12_candidate_image_naming_is_the_products(allowlist: dict[str, Any]) -> None:
    from harness_engines.release import candidate_image

    for entry in allowlist["engines"]:
        tags = [entry["image"], *(v for k, v in (entry.get("variants") or {}).items() if not k.startswith("_"))]
        for tag in tags:
            assert cat.candidate_image(tag, "9.9.9") == candidate_image(tag, "9.9.9")
    assert cat.candidate_image("reg.example:5000/pxh-x:1.0", "2.0") == candidate_image("reg.example:5000/pxh-x:1.0", "2.0")


def test_ops12_asset_url_moves_only_the_version(allowlist: dict[str, Any]) -> None:
    for kind in ("opencode", "codex", "acp-gemini", "acp-goose", "acp-opencode"):
        src = cat.source(allowlist, kind)
        old = cat.default_entry(allowlist, src["buildKind"])["asset"]["url"]
        new = cat.asset_url(allowlist, kind, "9.9.9")
        assert new.replace("9.9.9", cat.default_entry(allowlist, src["buildKind"])["version"]) == old
        assert f"/download/{src['tagPrefix']}9.9.9/" in new
    with pytest.raises(cat.CatalogError, match="no release asset"):
        cat.asset_url(allowlist, "claude_code", "9.9.9")


def test_wp91_an_npm_release_resolves_on_npm_and_moves_only_the_version(allowlist: dict[str, Any]) -> None:
    """Qwen Code ships as the registry's tarball: its latest is npm's version, its asset the same name at it."""
    src = cat.source(allowlist, "acp-qwen")
    assert (src["resolve"], src["package"], src["tagPrefix"], src["build"]) == ("npm", "@qwen-code/qwen-code", "", "release")
    assert cat.asset_url(allowlist, "acp-qwen", "9.9.9") == (
        "https://registry.npmjs.org/@qwen-code/qwen-code/-/qwen-code-9.9.9.tgz"
    )
    assert cat.decide(allowlist, "acp-qwen", "0.26.5")["action"] == "skip"
    assert cat.decide(allowlist, "acp-qwen", "0.27.0")["action"] == "candidate"


def test_ops12_binary_digest_reads_the_member_from_tar_and_zip(allowlist: dict[str, Any], tmp_path: Path) -> None:
    body = b"#!/bin/sh\necho engine\n"
    want = "sha256:" + hashlib.sha256(body).hexdigest()
    tgz = tmp_path / "codex.tar.gz"
    member = cat.default_entry(allowlist, "codex")["asset"]["member"]
    with tarfile.open(tgz, "w:gz") as archive:
        info = tarfile.TarInfo(member)
        info.size = len(body)
        archive.addfile(info, io.BytesIO(body))
    assert cat.binary_digest(allowlist, "codex", tgz) == want
    bundle = tmp_path / "gemini.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(cat.default_entry(allowlist, "acp-gemini")["asset"]["member"], body)
        archive.writestr("chunk-1.js", b"x")
    assert cat.binary_digest(allowlist, "acp-gemini", bundle) == want
    with pytest.raises(cat.CatalogError, match="is not in"):
        cat.binary_digest(allowlist, "opencode", tgz)


def test_ops12_every_gate_lane_is_a_real_suite_and_cc1s_unit_gate_runs_for_each_kind() -> None:
    for kind, lanes in cat.GATE_LANES.items():
        names = [lane[0] for lane in lanes]
        assert len(names) == len(set(names)), kind
        assert "candidate-isolation" in names, kind
        for _, path, _ in lanes:
            assert (PYTHON_ROOT / path).exists(), (kind, path)
    assert any("test_real_codex_conformance.py" in p for _, p, _ in cat.GATE_LANES["codex"])


# --- the draft allowlist change ------------------------------------------------------------------------------------


def test_ops12_the_draft_adds_an_opt_in_entry_after_the_default_and_never_writes(allowlist: dict[str, Any],
                                                                                  tmp_path: Path) -> None:
    before = ALLOWLIST.read_bytes()
    entry = _entry(allowlist, "codex")
    draft = cat.draft_allowlist(allowlist, entry)
    codex = [e for e in draft["engines"] if e["kind"] == "codex"]
    assert [e["version"] for e in codex] == [cat.default_entry(allowlist, "codex")["version"], "9.9.9"]
    added = codex[1]
    assert added["binaryDigest"] == D_BIN and added["asset"]["sha256"] == ASSET
    assert added["image"] == "pxh-codex-tools:9.9.9-ph7" and "-candidate" not in json.dumps(added["variants"])
    assert "rust-v9.9.9" in added["asset"]["url"]
    assert len(draft["engines"]) == len(allowlist["engines"]) + 1
    entry_file = tmp_path / "entry.json"
    entry_file.write_text(json.dumps(entry))
    patch = tmp_path / "allowlist.patch"
    assert cat.main(["draft-patch", str(entry_file), "--out", str(patch)], out=io.StringIO()) == 0
    text = patch.read_text()
    assert "--- a/backend/python/harness_engines/allowlist.json" in text and '+      "version": "9.9.9",' in text
    assert ALLOWLIST.read_bytes() == before
    assert cat.render(allowlist).encode() == before


def test_ops12_no_draft_for_an_invalid_entry(allowlist: dict[str, Any]) -> None:
    with pytest.raises(cat.CatalogError, match="already allow-listed"):
        cat.draft_allowlist(allowlist, _entry(allowlist, "opencode", over={
            "version": "1.18.34", "image": "pxh-opencode-tools:1.18.34-candidate"}))
