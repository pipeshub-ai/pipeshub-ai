"""Shared paths and source scans for the cohesion suite (EXECUTION-PLAN §4)."""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from types import ModuleType

PYTHON_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PYTHON_ROOT.parents[1]
NODE_HARNESS = REPO_ROOT / "backend" / "nodejs" / "apps" / "src" / "modules" / "agent-harness"

# The control layer that must embed in Loom as well as PipesHub (ARCHITECTURE principle 9).
CONTROL_LAYER = (
    "journal",
    "effects",
    "invoker",
    "engines",
    "sandbox",
    "workspace",
    "interactions",
    "access",
    "budget",
    "gateway",
    "artifacts",
    "skills",
    "credentials",
    "context",
    "telemetry",
    "audit",
    "knowledge",
    "mcp_hub",
    "memories",
    "tool_identity",
    "models",
    "security",
    "activity",
    "policy",
)


def modules(*roots: str, include_testing: bool = False) -> list[Path]:
    """Python source files under ``roots`` (relative to backend/python), generated code excluded."""
    found: list[Path] = []
    for root in roots:
        for path in sorted((PYTHON_ROOT / root).rglob("*.py")):
            parts = path.relative_to(PYTHON_ROOT).parts
            if "generated" in parts or "__pycache__" in parts:
                continue
            if not include_testing and "testing" in parts:
                continue
            found.append(path)
    return found


def dotted(path: Path) -> str:
    rel = path.relative_to(PYTHON_ROOT).with_suffix("")
    parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
    return ".".join(parts)


def top_level_classes(path: Path) -> list[ast.ClassDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node for node in tree.body if isinstance(node, ast.ClassDef)]


def is_protocol(node: ast.ClassDef) -> bool:
    for base in node.bases:
        target = base.value if isinstance(base, ast.Subscript) else base
        if isinstance(target, ast.Name) and target.id == "Protocol":
            return True
        if isinstance(target, ast.Attribute) and target.attr == "Protocol":
            return True
    return False


# An edition's overlay of the harness contracts (an extension point): beside its schemas, a file named like a
# contract document here (``flags.json``, ``config-surface.json``) adds entries to that document.
CONTRACT_OVERLAY = REPO_ROOT / "contracts" / "harness" / "ee"
# What names an entry of a contract document's list, so an overlay can add one but never redefine one.
_ENTRY_KEYS = ("name", "setting")


def with_overlay(base: dict[str, Any], name: str, overlay: Path = CONTRACT_OVERLAY) -> dict[str, Any]:
    """``base`` with the entries of ``overlay/name``, when present: each list gains the overlay's entries after its
    own, each map the overlay's keys. An overlay may only add: a section the document lacks, or an entry or key it
    already has, is an error. The overlay's ``description`` is its own."""
    path = overlay / name
    if not path.is_file():
        return base
    extra: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    merged = dict(base)
    for section, added in extra.items():
        if section == "description":
            continue
        if section not in base:
            raise ValueError(f"{path}: {section} is not a section of {name}")
        own = base[section]
        if isinstance(own, list) and isinstance(added, list):
            known = {_entry_key(entry) for entry in cast("list[Any]", own)}
            for entry in cast("list[Any]", added):
                if _entry_key(entry) in known:
                    raise ValueError(f"{path}: {section} already has {_entry_key(entry)}")
            merged[section] = [*own, *added]
        elif isinstance(own, dict) and isinstance(added, dict):
            repeated = set(cast("dict[str, Any]", own)) & set(cast("dict[str, Any]", added))
            if repeated:
                raise ValueError(f"{path}: {section} already has {sorted(repeated)}")
            merged[section] = {**own, **added}
        else:
            raise ValueError(f"{path}: {section} can only be added to as a list or a map")
    return merged


def _entry_key(entry: Any) -> str:  # noqa: ANN401 - a JSON value
    if isinstance(entry, dict):
        for key in _ENTRY_KEYS:
            if key in entry:
                return str(cast("dict[str, Any]", entry)[key])
    return json.dumps(entry, sort_keys=True)


@cache
def _codegen() -> ModuleType:
    """``contracts/harness/codegen/generate.py``, which reads a schema as the generated models do."""
    spec = importlib.util.spec_from_file_location(
        "contracts_codegen_generate", REPO_ROOT / "contracts" / "harness" / "codegen" / "generate.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def contract_schema(path: Path) -> dict[str, Any]:
    """A contract schema with the properties an edition's overlay adds to it (``codegen/extensions.py``)."""
    loaded: dict[str, Any] = _codegen().schema_doc(path)
    return loaded


def policy_dimensions() -> list[dict[str, Any]]:
    """The dimension table (``policy-dimensions.json``) with the rows an edition's overlay adds."""
    gen = _codegen()
    entry = next(d for d in gen.DATA_FILES if d.constant == "POLICY_DIMENSIONS")
    path = REPO_ROOT / "contracts" / "harness" / entry.group / entry.path
    rows: list[dict[str, Any]] = gen.extended_data(entry, json.loads(path.read_text(encoding="utf-8")), gen.OVERLAY_ROOT)[
        "dimensions"
    ]
    return rows
