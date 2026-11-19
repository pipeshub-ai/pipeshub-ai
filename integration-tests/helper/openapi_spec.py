"""The API spec the stack serves, for the checks that compare replies with it.

That is ``pipeshub-openapi.yaml`` plus any spec file a downstream build merges
into it at start-up (``ApiDocsService.registerSpecOverlay``), for routes and
fields this repository does not have. Such a build names its files in a suite
extension (``helper/suite_extensions.py``): ``openapi_overlays.py`` defining
``OVERLAYS``, a sequence of paths. They are merged in order, the way the server
merges them, so a reply is checked against the spec the server documents.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from helper import suite_extensions

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO_ROOT / "backend" / "nodejs" / "apps" / "src" / "modules" / "api-docs" / "pipeshub-openapi.yaml"

# Keys that would write through to Object.prototype in the server's merge, which skips them.
_SKIPPED_KEYS = frozenset({"__proto__", "constructor", "prototype"})


def merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    """``mergeSpec`` in docs.service.ts: objects merge key by key, lists keep
    every distinct item, anything else is replaced."""
    for key, value in source.items():
        if key in _SKIPPED_KEYS:
            continue
        current = target.get(key)
        if isinstance(value, dict):
            into = current if isinstance(current, dict) else {}
            merge(into, value)
            target[key] = into
        elif isinstance(current, list) and isinstance(value, list):
            seen = {json.dumps(item, default=str) for item in current}
            target[key] = [*current, *(item for item in value if json.dumps(item, default=str) not in seen)]
        else:
            target[key] = value


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"OpenAPI spec not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    if not isinstance(doc, dict):
        raise ValueError(f"OpenAPI YAML root must be a mapping: {path}")
    return doc


def overlay_paths(root: Path = suite_extensions.ROOT) -> list[Path]:
    return [Path(p) for module in suite_extensions.modules("openapi_overlays", root) for p in module.OVERLAYS]


def load_spec(root: Path = suite_extensions.ROOT) -> dict[str, Any]:
    """A fresh copy of the merged spec; callers cache it."""
    spec = _read(SPEC_PATH)
    for path in overlay_paths(root):
        merge(spec, _read(path))
    return spec
