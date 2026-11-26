"""C5 with an edition's schema extensions (``contracts/harness/ee/**/*.extends.json``, ``codegen/extensions.py``).

A schema object this repository marks ``x-extensible`` takes the properties an overlay adds to it, and the
dimension table takes the rows an overlay adds: they add, never redefine. The generated models then accept the
added members; without an overlay nothing changes.
"""

from __future__ import annotations

import importlib.util
import json
from typing import TYPE_CHECKING, Any

import pytest

from .support import CONTRACTS, CONTROL_PLANE, REPO, generator
from .test_c5_codegen_drift import _skip_or_fail, _ts_unavailable

if TYPE_CHECKING:
    from pathlib import Path

GENERATE = generator()
LAYER = CONTRACTS / "core" / "policy" / "policy-layer.schema.json"
LAYER_ID = "https://schemas.pipeshub.com/harness/v2/core/policy/policy-layer.schema.json"
EXTENSIBLE = (
    LAYER,
    CONTRACTS / "ext" / "pipeshub" / "api" / "agent-config.schema.json",
    CONTROL_PLANE / "live-layer.schema.json",
)


def _write(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def _extension(overlay: Path, **extra: Any) -> Path:  # noqa: ANN401 - JSON
    return _write(
        overlay / "core" / "policy" / "policy-layer.extends.json",
        {
            "extends": LAYER_ID,
            "add": [{"at": "", "properties": {"editionPart": {"$ref": "#/$defs/EditionPart"}}}],
            "$defs": {
                "EditionPart": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"limit": {"type": "integer", "minimum": 0}},
                }
            },
            **extra,
        },
    )


def _extended(overlay: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = GENERATE.extensions.load(LAYER, GENERATE.extensions.Extensions.of(overlay))
    return loaded


def test_the_extensible_schemas_are_marked_and_read_as_written_without_an_overlay(tmp_path: Path) -> None:
    for path in EXTENSIBLE:
        written = json.loads(path.read_text(encoding="utf-8"))
        assert GENERATE.extensions.load(path, GENERATE.extensions.Extensions.of(tmp_path / "absent")) == written
        assert "x-extensible" in json.dumps(written), path


def test_an_extension_adds_properties_and_defs(tmp_path: Path) -> None:
    _extension(tmp_path)
    layer = _extended(tmp_path)
    assert layer["properties"]["editionPart"] == {"$ref": "#/$defs/EditionPart"}
    assert "EditionPart" in layer["$defs"]
    assert "editionPart" not in json.loads(LAYER.read_text(encoding="utf-8"))["properties"]


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"add": [{"at": "", "properties": {"budgets": {"type": "object"}}}]}, "already defined"),
        ({"$defs": {"LayerName": {"type": "string"}}}, "already defined"),
        ({"add": [{"at": "/$defs/LayerScope", "properties": {"x": {"type": "string"}}}]}, "not marked"),
        ({"add": [{"at": "/nowhere", "properties": {"x": {"type": "string"}}}]}, "names nothing"),
    ],
)
def test_an_extension_that_would_redefine_is_refused(tmp_path: Path, extra: dict[str, Any], message: str) -> None:
    _extension(tmp_path, **extra)
    with pytest.raises(SystemExit, match=message):
        _extended(tmp_path)


def test_an_extension_of_a_schema_nobody_declares_is_refused(tmp_path: Path) -> None:
    _write(tmp_path / "core" / "x.extends.json", {"extends": "https://schemas.example.test/none", "add": []})
    with pytest.raises(SystemExit, match="no schema declares"):
        GENERATE.extensions.Extensions.of(tmp_path).check_targets(set(GENERATE.schema_ids()))


def test_an_overlay_adds_dimension_rows_and_refuses_a_repeated_one(tmp_path: Path) -> None:
    entry = next(d for d in GENERATE.DATA_FILES if d.constant == "POLICY_DIMENSIONS")
    base = json.loads((CONTRACTS / entry.group / entry.path).read_text(encoding="utf-8"))
    assert GENERATE.extended_data(entry, base, tmp_path) is base
    row = {"path": "editionPart.limit", "kind": "ceiling", "order": {"kind": "numeric"}, "exposure": ["org"]}
    _write(tmp_path / entry.group / entry.path, {"dimensions": [row]})
    assert GENERATE.extended_data(entry, base, tmp_path)["dimensions"][-1] == row
    _write(tmp_path / entry.group / entry.path, {"dimensions": [{**row, "path": base["dimensions"][0]["path"]}]})
    with pytest.raises(GENERATE.constants.ConstantsError, match="already defined"):
        GENERATE.extended_data(entry, base, tmp_path)


@pytest.mark.skipif(importlib.util.find_spec("datamodel_code_generator") is None, reason="datamodel-code-generator not installed")
def test_python_models_accept_what_an_extension_adds(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay"
    rules_id = "https://schemas.example.test/overlay/core/policy/edition-rules.schema.json"
    _write(
        overlay / "core" / "policy" / "edition-rules.schema.json",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": rules_id,
            "title": "EditionRules",
            "type": "object",
            "additionalProperties": False,
            "properties": {"levels": {"type": "array", "items": {"type": "string"}}},
        },
    )
    _extension(overlay)
    extension = overlay / "core" / "policy" / "policy-layer.extends.json"
    data = json.loads(extension.read_text(encoding="utf-8"))
    data["add"][0]["properties"]["editionRules"] = {"$ref": rules_id}
    _write(extension, data)
    into = tmp_path / "out"
    GENERATE.main(["--skip-ts", "--skip-vectors", "--into", str(into), "--overlay", str(overlay)])
    source = (into / GENERATE.GROUPS[0].python_out / "policy" / "policy_layer.py").read_text(encoding="utf-8")
    assert "edition_part" in source and "class EditionPart" in source
    # A built-in module names an overlay's module by its package: the overlay sits beneath it only in staging.
    assert "from harness_core.contracts.generated.ee.policy import edition_rules" in source
    assert "from ...ee" not in source
    assert GENERATE.EXTENSIONS.ids() == GENERATE.extensions.Extensions.of(GENERATE.OVERLAY).ids(), "main restores"


def test_zod_models_accept_what_an_extension_adds(tmp_path: Path) -> None:
    reason = _ts_unavailable()
    if reason is not None:
        _skip_or_fail(reason)
    overlay = tmp_path / "overlay"
    _extension(overlay)
    # Another root's schema extended with a ref into the core overlay, by its $id.
    part_id = "https://schemas.example.test/overlay/core/edition-rules.schema.json"
    _write(
        overlay / "core" / "edition-rules.schema.json",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": part_id,
            "title": "EditionRules",
            "type": "object",
            "additionalProperties": False,
            "properties": {"levels": {"type": "array", "items": {"type": "string"}}},
        },
    )
    live = json.loads((CONTROL_PLANE / "live-layer.schema.json").read_text(encoding="utf-8"))
    _write(
        overlay / "control-plane" / "live-layer.extends.json",
        {"extends": live["$id"], "add": [{"at": "", "properties": {"editionRules": {"$ref": part_id}}}]},
    )
    into = tmp_path / "out"
    GENERATE.main(["--skip-python", "--skip-vectors", "--into", str(into), "--overlay", str(overlay)])
    node = into / "backend" / "nodejs" / "apps" / "src" / "modules" / "agent-harness" / "contracts" / "generated"
    assert '"editionPart"' in (node / "core" / "policy" / "policy-layer.ts").read_text(encoding="utf-8")
    plane = into / "backend" / "nodejs" / "apps" / "src" / "modules" / "control-plane" / "contracts" / "generated"
    assert '"editionRules"' in (plane / "live-layer.ts").read_text(encoding="utf-8")
    built_in = REPO / "backend" / "nodejs" / "apps" / "src" / "modules" / "agent-harness" / "contracts" / "generated"
    assert '"editionPart"' not in (built_in / "core" / "policy" / "policy-layer.ts").read_text(encoding="utf-8")


# An enumeration marked ``x-extensible-enum`` takes the values an overlay adds; an overlay's event types join the
# catalog of the group they extend; its error codes join that group's catalog. None may redefine.
SCOPE = "/$defs/ToolDisclosureMode"


def _enum_extension(overlay: Path, values: list[str], at: str = SCOPE) -> Path:
    return _write(
        overlay / "core" / "policy" / "policy-layer.extends.json",
        {"extends": LAYER_ID, "add": [{"at": at, "enum": values}]},
    )


def _mark_scope(tmp_path: Path) -> Path:
    """A copy of the policy layer whose ``ToolDisclosureMode`` is marked, the overlay's target in these cases."""
    layer = json.loads(LAYER.read_text(encoding="utf-8"))
    layer["$defs"]["ToolDisclosureMode"]["x-extensible-enum"] = True
    return _write(tmp_path / "marked" / "policy-layer.schema.json", layer)


def test_an_extension_adds_enum_values_to_a_marked_enumeration(tmp_path: Path) -> None:
    marked = _mark_scope(tmp_path)
    before = json.loads(marked.read_text(encoding="utf-8"))["$defs"]["ToolDisclosureMode"]["enum"]
    _enum_extension(tmp_path / "overlay", ["editionMode"])
    loaded = GENERATE.extensions.load(marked, GENERATE.extensions.Extensions.of(tmp_path / "overlay"))
    assert loaded["$defs"]["ToolDisclosureMode"]["enum"] == [*before, "editionMode"]


@pytest.mark.parametrize(
    ("values", "at", "message"),
    [
        (["eager"], SCOPE, "already defined"),
        (["x"], "/$defs/LayerName", "not an enumeration marked"),
        ([], SCOPE, "list of strings"),
    ],
)
def test_an_enum_extension_that_would_redefine_is_refused(tmp_path: Path, values: list[str], at: str, message: str) -> None:
    marked = _mark_scope(tmp_path)
    _enum_extension(tmp_path / "overlay", values, at)
    with pytest.raises(SystemExit, match=message):
        GENERATE.extensions.load(marked, GENERATE.extensions.Extensions.of(tmp_path / "overlay"))


def test_an_addition_names_properties_or_values_not_both(tmp_path: Path) -> None:
    _write(
        tmp_path / "core" / "policy" / "policy-layer.extends.json",
        {"extends": LAYER_ID, "add": [{"at": "", "properties": {}, "enum": ["x"]}]},
    )
    with pytest.raises(SystemExit, match="either its `properties` or its `enum`"):
        _extended(tmp_path)


def _edition_event(overlay: Path, type_: str = "x.pipeshub.edition.noted", title: str = "PipeshubEditionNotedEvent") -> Path:
    return _write(
        overlay / "ext" / "pipeshub" / "events" / f"{type_}.schema.json",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"https://schemas.example.test/overlay/ext/pipeshub/events/{type_}.schema.json",
            "title": title,
            "x-writer": ["worker"],
            "allOf": [{"$ref": "https://schemas.pipeshub.com/harness/v2/core/envelope.schema.json#/$defs/InlineEnvelope"}],
            "properties": {
                "type": {"const": type_},
                "writer": {"const": "worker"},
                "data": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["note"],
                    "properties": {"note": {"type": "string"}},
                },
            },
        },
    )


def test_an_overlay_event_type_joins_the_catalog_of_the_group_it_extends() -> None:
    entries = GENERATE._event_entries(GENERATE.GROUPS[1])
    assert all(module.startswith("app.harness.contracts.generated.pipeshub.events.") for module, *_ in entries)


def test_an_overlay_event_that_redefines_a_type_is_refused(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay"
    _edition_event(overlay, "x.pipeshub.answer.finalized", "PipeshubEditionAnswer")
    groups = GENERATE.all_groups(overlay)
    with pytest.raises(SystemExit, match="already defined"):
        GENERATE._event_entries(GENERATE.GROUPS[1], groups)


def test_an_overlay_adds_error_codes_and_refuses_a_repeated_one(tmp_path: Path) -> None:
    group = GENERATE.GROUPS[1]
    schema = json.loads((CONTRACTS / "core" / GENERATE.constants.ERROR_CATALOG_SCHEMA).read_text(encoding="utf-8"))
    base = GENERATE.extended_errors(group, schema, tmp_path)
    assert base == GENERATE.constants.error_catalog(group.source, schema)
    added = {**base["codes"][0], "code": "EDITION_REFUSED"}
    _write(tmp_path / group.key / "errors.json", {**base, "codes": [added]})
    assert GENERATE.extended_errors(group, schema, tmp_path)["codes"][-1] == added
    _write(tmp_path / group.key / "errors.json", {**base, "codes": [base["codes"][0]]})
    with pytest.raises(GENERATE.constants.ConstantsError, match="already defined"):
        GENERATE.extended_errors(group, schema, tmp_path)


@pytest.mark.skipif(importlib.util.find_spec("datamodel_code_generator") is None, reason="datamodel-code-generator not installed")
def test_python_catalog_holds_an_overlay_event(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay"
    _edition_event(overlay)
    into = tmp_path / "out"
    GENERATE.main(["--skip-ts", "--skip-vectors", "--into", str(into), "--overlay", str(overlay)])
    catalog = (into / GENERATE.GROUPS[1].python_out / "event_catalog.py").read_text(encoding="utf-8")
    assert (
        "from app.harness.contracts.generated.pipeshub.ee.events.x_pipeshub_edition_noted import PipeshubEditionNotedEvent"
        in catalog
    )
    assert '"x.pipeshub.edition.noted": frozenset({"worker"}),' in catalog
    assert not (into / GENERATE.GROUPS[1].python_out / "ee" / "event_catalog.py").exists()


def test_zod_catalog_holds_an_overlay_event(tmp_path: Path) -> None:
    reason = _ts_unavailable()
    if reason is not None:
        _skip_or_fail(reason)
    overlay = tmp_path / "overlay"
    _edition_event(overlay)
    into = tmp_path / "out"
    GENERATE.main(["--skip-python", "--skip-vectors", "--into", str(into), "--overlay", str(overlay)])
    node = into / "backend" / "nodejs" / "apps" / "src" / "modules" / "agent-harness" / "contracts" / "generated"
    barrel = (node / "ext" / "pipeshub" / "index.ts").read_text(encoding="utf-8")
    # Imported from the overlay's barrel directory beneath this one.
    overlay_module = "/".join(("..", "..", GENERATE.OVERLAY_DIR, "ext", "pipeshub", "events", "x-pipeshub-edition-noted"))
    assert f"import {{ pipeshubEditionNotedEventSchema }} from '{overlay_module}';" in barrel
    assert '"x.pipeshub.edition.noted": pipeshubEditionNotedEventSchema,' in barrel
    # Its own barrel exports the schema; the journal's catalog is the extended group's alone.
    assert '"x.pipeshub.edition.noted"' not in (node / "ee" / "ext" / "pipeshub" / "index.ts").read_text(encoding="utf-8")
