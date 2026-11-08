"""Extension rows are added to the suite's tables, and can only add.

``helper/suite_extensions.py`` lets a downstream build add admin-route rows,
test-world groups and edge-schema fields without editing the tables here. These
check that nothing is merged when there is no extension, that an extension's
rows land, and that one cannot replace or loosen what is already there.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from helper import suite_extensions
from validation.graph_edge_validator import load_edge_schema

pytestmark = pytest.mark.unit


def _extension(root: Path, name: str, files: dict[str, str]) -> Path:
    directory = root / name / suite_extensions.DIR_NAME
    for rel, body in files.items():
        path = directory / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")
    return directory


def test_without_an_extension_the_tables_are_used_as_written(tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()
    assert suite_extensions.extension_dirs(tmp_path) == []
    found = suite_extensions.modules("table", tmp_path)
    assert found == []
    assert suite_extensions.add_rows((1, 2), "ROWS", found, key=int) == (1, 2)
    assert suite_extensions.add_items({"a": 1}, "ITEMS", found) == {"a": 1}


def test_an_extension_adds_rows_and_items(tmp_path: Path) -> None:
    _extension(tmp_path, "extra", {"table.py": """
        ROWS = (3,)
        ITEMS = {"b": 2}
    """})
    found = suite_extensions.modules("table", tmp_path)
    assert suite_extensions.add_rows((1, 2), "ROWS", found, key=int) == (1, 2, 3)
    assert suite_extensions.add_items({"a": 1}, "ITEMS", found) == {"a": 1, "b": 2}
    assert suite_extensions.add_rows((1,), "OTHER", found, key=int) == (1,)


def test_an_extension_cannot_replace_a_row_or_an_item(tmp_path: Path) -> None:
    _extension(tmp_path, "extra", {"clash.py": """
        ROWS = (2,)
        ITEMS = {"a": "looser"}
    """})
    found = suite_extensions.modules("clash", tmp_path)
    with pytest.raises(ValueError, match="already there"):
        suite_extensions.add_rows((1, 2), "ROWS", found, key=int)
    with pytest.raises(ValueError, match="already there"):
        suite_extensions.add_items({"a": "strict"}, "ITEMS", found)


def test_an_extension_module_can_import_its_siblings(tmp_path: Path) -> None:
    _extension(tmp_path, "extra", {
        "common.py": "ROWS = (3,)\n",
        "sibling_table.py": "from .common import ROWS\n",
    })
    found = suite_extensions.modules("sibling_table", tmp_path)
    assert suite_extensions.add_rows((1, 2), "ROWS", found, key=int) == (1, 2, 3)


@pytest.mark.parametrize(("groups", "clash"), [
    ({"more": ("org", "extra_key")}, "org"),
    ({"more": ("extra_key",), "again": ("extra_key",)}, "extra_key"),
    ({"more": ("made_fresh",)}, "made_fresh"),
], ids=["key-of-a-base-group", "key-of-another-new-group", "reserved-key"])
def test_an_extension_group_cannot_claim_a_key_held_elsewhere(
    tmp_path: Path, groups: dict[str, tuple[str, ...]], clash: str,
) -> None:
    _extension(tmp_path, "extra", {"world.py": f"GROUPS = {groups!r}\n"})
    found = suite_extensions.modules("world", tmp_path)
    with pytest.raises(ValueError, match=repr(clash)):
        suite_extensions.add_groups(
            {"identity": ("org",)}, "GROUPS", found, reserved=frozenset({"made_fresh"}))


def test_an_extension_group_with_new_keys_is_added(tmp_path: Path) -> None:
    _extension(tmp_path, "extra", {"world_ok.py": 'GROUPS = {"more": ("extra_key",)}\n'})
    found = suite_extensions.modules("world_ok", tmp_path)
    assert suite_extensions.add_groups({"identity": ("org",)}, "GROUPS", found) == {
        "identity": ("org",), "more": ("extra_key",),
    }


_BASE_EDGE = """
    name: sample_edge
    fields:
      kind:
        required: false
        type: string
        enum:
          - ONE
      createdAtTimestamp:
        required: true
        type: integer
"""


def _edge_dirs(tmp_path: Path, extra: str) -> tuple[Path, Path]:
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    (schema_dir / "sample.yaml").write_text(textwrap.dedent(_BASE_EDGE), encoding="utf-8")
    root = tmp_path / "root"
    _extension(root, "extra", {"schemas/edges/sample.yaml": extra})
    return schema_dir, root


def test_an_edge_extension_adds_fields_and_enum_values(tmp_path: Path) -> None:
    schema_dir, root = _edge_dirs(tmp_path, """
        name: sample_edge
        fields:
          kind:
            required: false
            type: string
            enum:
              - TWO
          score:
            required: false
            type: number
    """)
    schema = load_edge_schema("sample.yaml", schema_dir=schema_dir, root=root)
    assert schema.fields["kind"].enum == ["ONE", "TWO"]
    assert schema.fields["score"].type == "number"
    assert schema.fields["createdAtTimestamp"].required is True
    plain = load_edge_schema("sample.yaml", schema_dir=schema_dir, root=tmp_path / "none")
    assert set(plain.fields) == {"kind", "createdAtTimestamp"}


@pytest.mark.parametrize("change", [
    "createdAtTimestamp:\n    required: false\n    type: integer",
    "kind:\n    required: true\n    type: string\n    enum:\n      - TWO",
    "kind:\n    required: false\n    type: integer\n    enum:\n      - TWO",
], ids=["drops-required", "changes-required", "changes-type"])
def test_an_edge_extension_cannot_loosen_a_field(tmp_path: Path, change: str) -> None:
    body = "name: sample_edge\nfields:\n  " + change.replace("\n", "\n  ") + "\n"
    schema_dir, root = _edge_dirs(tmp_path, body)
    with pytest.raises(ValueError, match="sample.yaml"):
        load_edge_schema("sample.yaml", schema_dir=schema_dir, root=root)
