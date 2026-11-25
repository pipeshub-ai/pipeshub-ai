"""C5 with an edition's schema overlay (``contracts/harness/ee/<group>/``, an extension point).

Present, its schemas are generated beside each group's own, into an ``ee`` package or directory of every target,
and the built-in output does not change; absent, nothing is generated for it. The repository's own targets carry an
overlay directory exactly when the overlay is present, so C5 compares only the roots present.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import shutil
import sys
from typing import TYPE_CHECKING, Any

import pytest

from .support import REPO, generator
from .test_c5_codegen_drift import _skip_or_fail, _tree, _ts_unavailable

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

GENERATE = generator()
COMMON = "https://schemas.pipeshub.com/harness/v2/core/common.schema.json"
OVERLAY_ID = "https://schemas.example.test/overlay"


def _write(path: Path, schema: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")


def _overlay(root: Path) -> Path:
    """A core schema that refs a sibling and a built-in schema, and a control-plane one that refs a built-in; beside
    the repository's own overlay, when it has one, whose additions the built-in output already holds."""
    if GENERATE.OVERLAY.is_dir():
        shutil.copytree(GENERATE.OVERLAY, root, dirs_exist_ok=True)
    _write(
        root / "core" / "sample" / "edition-part.schema.json",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"{OVERLAY_ID}/core/sample/edition-part.schema.json",
            "title": "EditionPart",
            "type": "object",
            "additionalProperties": False,
            "required": ["label"],
            "properties": {"label": {"type": "string", "minLength": 1}},
        },
    )
    _write(
        root / "core" / "sample" / "edition-thing.schema.json",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"{OVERLAY_ID}/core/sample/edition-thing.schema.json",
            "title": "EditionThing",
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "part"],
            "properties": {
                "id": {"$ref": f"{COMMON}#/$defs/Id"},
                "part": {"$ref": "./edition-part.schema.json"},
                "kind": {"enum": ["one", "two"]},
            },
        },
    )
    _write(
        root / "control-plane" / "edition-setting.schema.json",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"{OVERLAY_ID}/control-plane/edition-setting.schema.json",
            "title": "EditionSetting",
            "type": "object",
            "additionalProperties": False,
            "required": ["orgId"],
            "properties": {"orgId": {"$ref": f"{COMMON}#/$defs/Id"}},
        },
    )
    return root


def _with_own(expected: list[str], committed: Path, pattern: str) -> list[str]:
    """``expected`` with the files the repository's own overlay generates under ``committed``, if any."""
    own = committed / GENERATE.OVERLAY_DIR
    found = {p.relative_to(committed).as_posix() for p in own.rglob(pattern)} if own.is_dir() else set()
    return sorted({*expected, *found})


def _same_built_in(committed: Path, fresh: Path) -> None:
    """The two trees agree outside their overlay directory (the repository's may hold an edition's own)."""

    def built_in(root: Path) -> dict[str, bytes]:
        return {name: data for name, data in _tree(root).items() if not name.startswith(f"{GENERATE.OVERLAY_DIR}/")}

    assert built_in(committed) == built_in(fresh), f"an overlay changed the built-in output under {committed}"


def _imported(monkeypatch: pytest.MonkeyPatch, package: str, generated: Path, module: str) -> ModuleType:
    """``package.ee.<module>`` from a generated tree outside the repository, imported by its own absolute names."""
    parent = importlib.import_module(package)
    # First: the repository may hold an overlay package of its own, which the generated one replaces here.
    monkeypatch.setattr(parent, "__path__", [str(generated), *parent.__path__])
    for name in [n for n in sys.modules if n.startswith(f"{package}.{GENERATE.OVERLAY_DIR}")]:
        monkeypatch.delitem(sys.modules, name)
    loaded = importlib.import_module(f"{package}.{GENERATE.OVERLAY_DIR}.{module}")
    for name in [n for n in sys.modules if n.startswith(f"{package}.{GENERATE.OVERLAY_DIR}")]:
        monkeypatch.delitem(sys.modules, name)
    return loaded


def test_without_an_overlay_the_groups_are_the_built_in_ones(tmp_path: Path) -> None:
    assert GENERATE.all_groups(None) == GENERATE.GROUPS
    assert GENERATE.all_groups(tmp_path / "absent") == GENERATE.GROUPS
    (tmp_path / "empty" / "core").mkdir(parents=True)
    assert GENERATE.all_groups(tmp_path / "empty") == GENERATE.GROUPS


def test_an_overlay_adds_one_group_per_group_it_has_schemas_for(tmp_path: Path) -> None:
    groups = [g for g in GENERATE.overlay_groups(_overlay(tmp_path / "overlay")) if g.key in ("ee/core", "ee")]
    assert [(g.key, g.root, g.python_package) for g in groups] == [
        ("ee/core", "harness", "harness_core.contracts.generated.ee"),
        ("ee", "control-plane", "app.control_plane.contracts.generated.ee"),
    ]
    assert all(g.overlay for g in groups)
    assert [g.python_out for g in groups] == [
        "backend/python/harness_core/contracts/generated/ee",
        "backend/python/app/control_plane/contracts/generated/ee",
    ]


def test_an_id_two_schemas_declare_is_refused(tmp_path: Path) -> None:
    overlay = _overlay(tmp_path / "overlay")
    _write(overlay / "core" / "clash.schema.json", {"$id": COMMON, "title": "Clash", "type": "object"})
    with pytest.raises(SystemExit, match="declared by both"):
        GENERATE.schema_ids(GENERATE.all_groups(overlay))


def test_the_repository_carries_an_overlay_directory_only_for_an_overlay_present() -> None:
    present = {g.python_out for g in GENERATE.overlay_groups()}
    for group in GENERATE.GROUPS:
        out = f"{group.python_out}/{GENERATE.OVERLAY_DIR}"
        assert (REPO / out).is_dir() == (out in present), out


@pytest.mark.skipif(importlib.util.find_spec("datamodel_code_generator") is None, reason="datamodel-code-generator not installed")
def test_python_overlay_models_generate_beside_the_built_in_ones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    overlay = _overlay(tmp_path / "overlay")
    into = tmp_path / "out"
    GENERATE.main(["--skip-ts", "--skip-vectors", "--into", str(into), "--overlay", str(overlay)])
    for group in GENERATE.GROUPS:
        _same_built_in(REPO / group.python_out, into / group.python_out)
    core = into / GENERATE.GROUPS[0].python_out
    plane = into / GENERATE.GROUPS[2].python_out
    assert sorted(p.relative_to(core).as_posix() for p in (core / GENERATE.OVERLAY_DIR).rglob("*.py")) == _with_own(
        [
            "ee/__init__.py", "ee/sample/__init__.py", "ee/sample/edition_part.py",
            "ee/sample/edition_thing.py",
        ],
        REPO / GENERATE.GROUPS[0].python_out,
        "*.py",
    )
    source = (core / "ee" / "sample" / "edition_thing.py").read_text(encoding="utf-8")
    assert "from harness_core.contracts.generated.ee.sample import edition_part" in source
    assert "from ." not in source, "an overlay module imports by absolute package"
    thing = _imported(monkeypatch, "harness_core.contracts.generated", core, "sample.edition_thing")
    model = thing.EditionThing.model_validate({"id": "a1", "part": {"label": "x"}, "kind": "two"})
    assert model.part.label == "x"
    with pytest.raises(ValueError, match="extra"):
        thing.EditionThing.model_validate({"id": "a1", "part": {"label": "x"}, "extra": 1})
    setting = _imported(monkeypatch, "app.control_plane.contracts.generated", plane, "edition_setting")
    assert setting.EditionSetting.model_validate({"orgId": "o1"}).org_id == "o1"


def test_typescript_overlay_schemas_generate_into_an_ee_directory_with_a_barrel(tmp_path: Path) -> None:
    reason = _ts_unavailable()
    if reason is not None:
        _skip_or_fail(reason)
    overlay = _overlay(tmp_path / "overlay")
    into = tmp_path / "out"
    GENERATE.main(["--skip-python", "--skip-vectors", "--into", str(into), "--overlay", str(overlay)])
    for target in GENERATE.TS_TARGETS:
        out = into / target.out
        extra = out / GENERATE.OVERLAY_DIR
        assert extra.is_dir(), target.out
        files = sorted(p.relative_to(extra).as_posix() for p in extra.rglob("*.ts"))
        own = [f.removeprefix(f"{GENERATE.OVERLAY_DIR}/") for f in _with_own([], REPO / target.out, "*.ts")]
        if target.root == "harness":
            assert files == sorted({"core/index.ts", "core/sample/edition-part.ts", "core/sample/edition-thing.ts", *own})
            barrel = (extra / "core" / "index.ts").read_text(encoding="utf-8")
            assert "export { editionThingSchema } from './sample/edition-thing';" in barrel
        else:
            assert files == sorted({"edition-setting.ts", "index.ts", *own})
        _same_built_in(REPO / target.out, out)
