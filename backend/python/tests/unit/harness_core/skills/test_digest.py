"""SD-1..SD-5 (bundle and legacy pin digests) and BB-4 (invalid bundles write nothing)."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from harness_core.skills import (
    InMemorySkillContentSource,
    SkillBundleInvalid,
    SkillBundleLimits,
    SkillBundleRef,
    SkillPin,
    SkillRef,
    StoredSkillBundleBuilder,
    legacy_skill_pin_digest,
    skill_bundle_digest,
    validate_skill_files,
)
from harness_core.testing.clock import ManualClock
from harness_core.workspace import ObjectScope
from harness_core.workspace.memory import (
    InMemoryBinaryObjectStore,
    InMemoryKeyProvider,
    InMemoryWorkspaceStore,
)
from harness_core.workspace.sealing import SealedObjectStore

REPO = Path(__file__).resolve().parents[6]
VECTORS = json.loads((REPO / "contracts/harness/vectors/skill-bundles.json").read_text(encoding="utf-8"))
GOLDEN_SPEC = json.loads((REPO / "contracts/harness/vectors/pinned-run-spec.json").read_text(encoding="utf-8"))
PYTHON_ROOT = Path(__file__).resolve().parents[4]

FILES = {
    "SKILL.md": b"---\nname: report\n---\n# Report\n",
    "scripts/run.py": b"print('ok')\n",
    "assets/logo.png": b"\x89PNG\r\n\x1a\n\x00\xff",
}


def _decoded(files: dict[str, str]) -> dict[str, bytes]:
    return {path: base64.b64decode(content) for path, content in files.items()}


@pytest.mark.parametrize("vector", VECTORS["bundles"], ids=lambda v: v["description"])
def test_sd2_bundle_vectors(vector: dict[str, object]) -> None:
    files = _decoded(vector["files"])  # type: ignore[arg-type]
    assert skill_bundle_digest(validate_skill_files(files)) == vector["digest"]


@pytest.mark.parametrize("vector", VECTORS["legacyPins"], ids=lambda v: f"{v['name']}@{v['version']}")
def test_sd5_legacy_pin_vectors(vector: dict[str, str | None]) -> None:
    assert legacy_skill_pin_digest(str(vector["name"]), vector["version"]) == vector["digest"]


def test_sd5_the_golden_pinned_spec_holds_a_legacy_pin() -> None:
    skills = GOLDEN_SPEC["pinned"]["spec"]["extensions"]["x.pipeshub"]["skills"]
    assert skills == [{"name": "xlsx", "bundleDigest": legacy_skill_pin_digest("xlsx", "3")}]


def test_sd1_order_and_process_do_not_change_the_digest() -> None:
    reordered = dict(reversed(list(FILES.items())))
    assert skill_bundle_digest(reordered) == skill_bundle_digest(FILES)
    program = (
        "import sys; from harness_core.skills import skill_bundle_digest; "
        f"print(skill_bundle_digest({FILES!r}))"
    )
    digests = {
        subprocess.run(
            [sys.executable, "-c", program],
            cwd=PYTHON_ROOT,
            env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(PYTHON_ROOT)},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        for seed in ("1", "2", "random")
    }
    assert digests == {skill_bundle_digest(FILES)}


def test_sd4_every_byte_and_path_counts() -> None:
    base = skill_bundle_digest(FILES)
    assert skill_bundle_digest({**FILES, "scripts/run.py": b"print('ok!')\n"}) != base
    renamed = {("scripts/go.py" if k == "scripts/run.py" else k): v for k, v in FILES.items()}
    assert skill_bundle_digest(renamed) != base
    assert skill_bundle_digest({**FILES, "assets/empty": b""}) != base


async def test_sd3_the_digest_does_not_depend_on_the_object_store() -> None:
    scope = ObjectScope("tenant-a", "session-1")
    source = InMemorySkillContentSource()
    _ = source.publish("tenant-a", "report", FILES)
    hashes: set[str] = set()
    digests: set[str] = set()
    for objects in (InMemoryBinaryObjectStore(), SealedObjectStore(InMemoryBinaryObjectStore(), InMemoryKeyProvider())):
        workspaces = InMemoryWorkspaceStore(clock=ManualClock(datetime(2026, 10, 3, tzinfo=UTC)))
        builder = StoredSkillBundleBuilder(source=source, objects=objects, workspaces=workspaces)
        bundle = await builder.build(scope, SkillRef("report"))
        digests.add(bundle.digest)
        hashes.add((await workspaces.head(scope, bundle.workspace_id)).content_hash)
    assert digests == {skill_bundle_digest(FILES)}
    assert len(hashes) == 2


@pytest.mark.parametrize(
    "files",
    [
        {"scripts/run.py": b"pass"},
        {"SKILL.md": b"\xff\xfe not utf-8"},
        {"SKILL.md": b"# s", "../secret": b"no"},
        {"SKILL.md": b"# s", "assets\\secret": b"no"},
        {"SKILL.md": b"# s", "/abs": b"no"},
        {"SKILL.md": b"# s", "assets/Logo.png": b"one", "assets/logo.png": b"two"},
        {"SKILL.md": b"# s", **{f"f{i}": b"" for i in range(4)}},
        {"SKILL.md": b"# s", "big": b"x" * 11},
        {"SKILL.md": b"# s", "a": b"x" * 10, "b": b"x" * 10},
        {},
    ],
    ids=["no-entrypoint", "not-utf8", "traversal", "backslash", "absolute", "case-alias", "too-many", "file-quota",
         "total-quota", "empty"],
)
async def test_bb4_invalid_bundles_are_refused_before_any_write(files: dict[str, bytes]) -> None:
    objects = InMemoryBinaryObjectStore()
    builder = StoredSkillBundleBuilder(
        source=InMemorySkillContentSource(),
        objects=objects,
        workspaces=InMemoryWorkspaceStore(),
        limits=SkillBundleLimits(max_files=4, max_total_bytes=20, max_file_bytes=10),
    )
    with pytest.raises(SkillBundleInvalid):
        _ = await builder.store(ObjectScope("tenant-a"), "s", files)
    assert objects.object_count == 0


@pytest.mark.parametrize("name", ["", "a/b", "..", ".hidden", "a\\b", "x\x00"])
def test_a_skill_name_is_one_folder(name: str) -> None:
    with pytest.raises(SkillBundleInvalid):
        _ = SkillRef(name)
    with pytest.raises(SkillBundleInvalid):
        _ = SkillBundleRef(name, "sha256:" + "0" * 64, "w", 1, 1, 0)


@pytest.mark.parametrize("digest", ["latest", "sha256:abc", "sha256:" + "G" * 64, "md5:" + "0" * 64])
def test_a_pin_needs_a_sha256_digest(digest: str) -> None:
    with pytest.raises(SkillBundleInvalid):
        _ = SkillPin("s", digest)
