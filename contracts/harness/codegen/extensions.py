"""Properties an edition's schema overlay adds to this repository's schemas: they add, never redefine.

A schema object marked ``"x-extensible": true`` takes properties, and an enumeration marked
``"x-extensible-enum": true`` takes values, from an overlay file ``*.extends.json``::

    {
      "extends": "<the $id of the schema it extends>",
      "add": [
        {"at": "<JSON pointer to the marked object; empty for the root>", "properties": {...}},
        {"at": "<JSON pointer to the marked enumeration>", "enum": ["<value>", ...]}
      ],
      "$defs": {...}
    }

What it adds is read as if the base schema held it: a ``$ref`` in it resolves from the base file, its ``$defs``
join the base's, and its values follow the enumeration's own. A name or value the base already has, a target not
marked, or an ``$id`` no schema declares is refused, so an overlay cannot change what a document of this repository
means; it can only accept more. Without an overlay, every schema reads as written. The zod generator
(``generate-zod.mjs``) reads the schemas as this module extends them. An overlay's event schemas join the event
catalog of the group they extend (``generate.py``), and a type that group already declares is refused.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

SUFFIX = ".extends.json"
MARK = "x-extensible"
ENUM_MARK = "x-extensible-enum"
_KEYS = {"$comment", "extends", "add", "$defs"}
_ADD_KEYS = {"at", "properties", "enum"}


class ExtensionError(SystemExit):
    """An overlay extension that would redefine, or that names nothing it may extend."""


def _add_values(target: dict[str, Any], values: object, at: str, where: str) -> None:
    if target.get(ENUM_MARK) is not True or not isinstance(target.get("enum"), list):
        raise ExtensionError(f"{where}: {at!r} is not an enumeration marked {ENUM_MARK}")
    if not isinstance(values, list) or not values or not all(isinstance(v, str) for v in values):
        raise ExtensionError(f"{where}: `enum` adds a list of strings")
    known: list[Any] = target["enum"]
    for value in values:
        if value in known:
            raise ExtensionError(f"{where}: value {value!r} is already defined")
        known.append(value)


def _pointer(doc: dict[str, Any], at: str, where: str) -> dict[str, Any]:
    node: Any = doc
    for part in [p.replace("~1", "/").replace("~0", "~") for p in at.lstrip("/").split("/")] if at else []:
        if not isinstance(node, dict) or part not in node:
            raise ExtensionError(f"{where}: {at!r} names nothing in the schema it extends")
        node = node[part]
    if not isinstance(node, dict):
        raise ExtensionError(f"{where}: {at!r} is not an object schema")
    return node


@dataclass
class Extensions:
    """The overlay's extensions, by the ``$id`` of the schema each extends."""

    by_id: dict[str, list[tuple[Path, dict[str, Any]]]] = field(default_factory=dict)

    @classmethod
    def of(cls, overlay: Path | None) -> Extensions:
        found = cls()
        if overlay is None or not overlay.is_dir():
            return found
        for path in sorted(overlay.rglob(f"*{SUFFIX}")):
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or set(data) - _KEYS or not isinstance(data.get("extends"), str):
                raise ExtensionError(f"{path}: an extension holds only {sorted(_KEYS)} and names the $id it extends")
            found.by_id.setdefault(data["extends"], []).append((path, data))
        return found

    def ids(self) -> set[str]:
        return set(self.by_id)

    def apply(self, doc: dict[str, Any], source: Path | str = "") -> dict[str, Any]:
        """``doc`` with every extension of its ``$id`` added; ``doc`` itself when none extends it."""
        extensions = self.by_id.get(doc.get("$id", ""), [])
        if not extensions:
            return doc
        out = copy.deepcopy(doc)
        for path, data in extensions:
            where = f"{path} (extends {source or doc.get('$id')})"
            for add in data.get("add", []):
                if not isinstance(add, dict) or set(add) - _ADD_KEYS or ("properties" in add) == ("enum" in add):
                    raise ExtensionError(f"{where}: each addition names `at` and either its `properties` or its `enum`")
                target = _pointer(out, add.get("at", ""), where)
                if "enum" in add:
                    _add_values(target, add["enum"], add.get("at", ""), where)
                    continue
                if not isinstance(add["properties"], dict):
                    raise ExtensionError(f"{where}: `properties` is an object")
                if target.get(MARK) is not True:
                    raise ExtensionError(f"{where}: {add.get('at', '')!r} is not marked {MARK}")
                properties = target.setdefault("properties", {})
                for name, schema in add["properties"].items():
                    if name in properties:
                        raise ExtensionError(f"{where}: property {name!r} is already defined")
                    properties[name] = copy.deepcopy(schema)
            defs = out.setdefault("$defs", {})
            for name, schema in data.get("$defs", {}).items():
                if name in defs:
                    raise ExtensionError(f"{where}: $defs/{name} is already defined")
                defs[name] = copy.deepcopy(schema)
        return out

    def check_targets(self, ids: set[str]) -> None:
        """Refuses an extension of an ``$id`` no schema declares: it would extend nothing, silently."""
        for key, entries in self.by_id.items():
            if key not in ids:
                raise ExtensionError(f"{entries[0][0]}: extends {key}, which no schema declares")


def load(path: Path, extensions: Extensions) -> dict[str, Any]:
    """A schema file as this build reads it: as written, plus what the overlay adds to it."""
    return extensions.apply(json.loads(path.read_text(encoding="utf-8")), path)
