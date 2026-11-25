"""An edition's overlay of a test's own lists (an extension point).

Several tests scan the source tree and keep a list of the sites they accept, each with its reason: the org-context
and structural-writer ratchets, the model funnel, the usage convention, the record-access fake. Another edition that
builds on this tree adds sites of its own. It lists them in ``tests/ee/ratchets/<name>.json``, beside the tests,
instead of editing the lists here. A checkout without that file reads only the lists here.

An overlay only adds: an entry the list here already has is an error, so an edition cannot loosen a reason this
repository gives.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OVERLAY_DIR = Path(__file__).resolve().parent / "ee" / "ratchets"
PYTHON_ROOT = Path(__file__).resolve().parents[1]
# Another edition's own code, when this checkout carries it; the lists here say nothing about it.
EDITION_TREES = (PYTHON_ROOT / "app" / "ee", PYTHON_ROOT / "ee")


def overlay(name: str, directory: Path = OVERLAY_DIR) -> dict[str, Any]:
    """The overlay ``name`` (``<directory>/<name>.json``), or ``{}`` when there is none."""
    path = directory / f"{name}.json"
    if not path.is_file():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: an overlay is a JSON object")
    return loaded


def extended[V](base: dict[str, V], name: str, section: str, directory: Path = OVERLAY_DIR) -> dict[str, V]:
    """``base`` with the entries of the overlay's ``section`` added. A repeated key is an error."""
    added = overlay(name, directory).get(section) or {}
    if not isinstance(added, dict):
        raise ValueError(f"{name}.json: {section} is a JSON object")
    repeated = sorted(set(base) & set(added))
    if repeated:
        raise ValueError(f"{name}.json: {section} already has {repeated}")
    return {**base, **added}


def edition_tree_present() -> bool:
    """Whether this checkout carries another edition's own code."""
    return any(tree.is_dir() for tree in EDITION_TREES)
