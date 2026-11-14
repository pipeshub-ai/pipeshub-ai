"""Freeze the contents of every hand-kept file-type list, read on the tree before the registry.

    python -m tests.unit.config.file_types.pin_main_lists [PATH]

It reads the lists the registry replaces, so it runs only on a tree that still has them (main
before the registry). ``test_registry_equivalence`` checks each derived list against this file.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

PINNED_LISTS = Path(__file__).with_name("pinned_lists.json")
_ROOT = Path(__file__).resolve().parents[4]


def _enum_value(node: ast.expr) -> str:
    from app.config.constants import arangodb

    if isinstance(node, ast.Constant):
        return node.value
    assert isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute)
    enum = getattr(arangodb, node.value.value.id)  # type: ignore[attr-defined]
    return getattr(enum, node.value.attr).value


def _assigned_literals(path: str, names: set[str]) -> dict[str, list[str]]:
    tree = ast.parse((_ROOT / path).read_text())
    found: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in names:
                elts = node.value.keys if isinstance(node.value, ast.Dict) else node.value.elts  # type: ignore[attr-defined]
                found[name] = [_enum_value(e) for e in elts]
    assert found.keys() == names, f"{path}: missing {names - found.keys()}"
    return found


def collect() -> dict[str, Any]:
    from app.config.constants import arangodb as a
    from app.modules.parsers import encryption
    from app.services.parsing import registry
    from app.services.parsing.providers import local_docling_parser as ldp
    from app.services.resource_governor import kinds, tiers
    from app.utils import image_utils, libreoffice_convert
    from tests.unit.config.file_types.gen_pinned_decisions import _node_map

    gate = _assigned_literals(
        "app/services/messaging/kafka/handlers/record.py", {"supported_mime_types", "supported_extensions"}
    )
    legacy = _assigned_literals("app/containers/utils/utils.py", {"parsers"})
    kind_by_ext = {
        e: kind.value
        for names, kind in (
            (kinds._PDF_EXTENSIONS, kinds.ParseKind.PDF),
            (kinds._EXCEL_EXTENSIONS, kinds.ParseKind.EXCEL_LOAD),
            (kinds._DOCLING_EXTENSIONS, kinds.ParseKind.DOCLING_DOCX),
            (kinds._CSV_EXTENSIONS, kinds.ParseKind.CSV),
            (kinds._TEXT_EXTENSIONS, kinds.ParseKind.TEXT),
            (kinds._CODE_EXTENSIONS, kinds.ParseKind.CODE),
        )
        for e in names
    }
    return {
        "gate_mimes": sorted(gate["supported_mime_types"]),
        "gate_extensions": sorted(gate["supported_extensions"]),
        "SUPPORTED_CODE_FILE_EXTENSIONS": sorted(a.SUPPORTED_CODE_FILE_EXTENSIONS),
        "CODE_FILE_MIME_TYPE_VALUES": sorted(a.CODE_FILE_MIME_TYPE_VALUES),
        "CODE_FILE_EXTENSION_VALUES": sorted(a.CODE_FILE_EXTENSION_VALUES),
        "EXCLUDABLE_FILE_EXTENSIONS": list(a.EXCLUDABLE_FILE_EXTENSIONS),
        "FILE_MIME_TYPES": {k: v.value for k, v in a.FILE_MIME_TYPES.items()},
        "RECONCILIATION_ENABLED_MIME_TYPES": sorted(a.RECONCILIATION_ENABLED_MIME_TYPES),
        "RECONCILIATION_ENABLED_EXTENSIONS": sorted(a.RECONCILIATION_ENABLED_EXTENSIONS),
        "_MIME_TO_FORMAT": dict(sorted(registry._MIME_TO_FORMAT.items())),
        "_EXT_TO_FORMAT": dict(sorted(registry._EXT_TO_FORMAT.items())),
        "HEAVY_EXTENSIONS": sorted(tiers.HEAVY_EXTENSIONS),
        "LIGHT_EXTENSIONS": sorted(tiers.LIGHT_EXTENSIONS),
        "HEAVY_MIME_TYPES": sorted(tiers.HEAVY_MIME_TYPES),
        "LIGHT_MIME_TYPES": sorted(tiers.LIGHT_MIME_TYPES),
        "kind_by_extension": dict(sorted(kind_by_ext.items())),
        "kind_by_mime": {k: v.value for k, v in sorted(kinds._MIME_KINDS.items())},
        "mime_to_extension": dict(sorted(image_utils.mime_to_extension.items())),
        "OOXML_EXTENSIONS": sorted(encryption.OOXML_EXTENSIONS),
        "OOXML_MIME_TYPES": sorted(encryption.OOXML_MIME_TYPES),
        "docling_formats": list(ldp._SUPPORTED_FORMATS),
        "docling_name_ext": dict(sorted(ldp._FORMAT_EXTS.items())),
        "libreoffice_probe_inputs": sorted(libreoffice_convert._PROBE_SAMPLES),
        "legacy_parser_extensions": sorted(legacy["parsers"]),
        "node_extension_to_mime": dict(sorted(_node_map().items())),
    }


def main(argv: list[str]) -> int:
    path = Path(argv[0]) if argv else PINNED_LISTS
    path.write_text(json.dumps(collect(), indent=1, sort_keys=False) + "\n")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
