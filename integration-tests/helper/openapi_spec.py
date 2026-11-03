"""The OpenAPI document the server under test publishes.

``pipeshub-openapi.yaml`` plus every ``*-overlay.yaml`` in an ``api-docs`` module under
``backend/nodejs/apps/src``, merged the way ``ApiDocsService`` merges registered
overlays at start-up. A build that adds routes or fields documents them in an overlay,
so its responses are checked against its own spec, not the base one.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

API_SRC = Path(__file__).resolve().parents[2] / "backend" / "nodejs" / "apps" / "src"
BASE_SPEC = API_SRC / "modules" / "api-docs" / "pipeshub-openapi.yaml"


def overlay_paths() -> list[Path]:
    return sorted(API_SRC.glob("**/api-docs/*-overlay.yaml"))


def merge_spec(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Objects merge key by key; arrays keep every distinct item; anything else is replaced."""
    for key, value in source.items():
        current = target.get(key)
        if isinstance(value, dict):
            into = current if isinstance(current, dict) else {}
            merge_spec(into, value)
            target[key] = into
        elif isinstance(current, list) and isinstance(value, list):
            seen = {json.dumps(item, sort_keys=True) for item in current}
            target[key] = current + [item for item in value if json.dumps(item, sort_keys=True) not in seen]
        else:
            target[key] = value


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    if not isinstance(doc, dict):
        raise TypeError(f"OpenAPI YAML root must be a mapping: {path}")
    return doc


@lru_cache(maxsize=1)
def load_spec() -> dict[str, Any]:
    """The merged document. Callers that change it must copy it first."""
    if not BASE_SPEC.is_file():
        raise FileNotFoundError(f"OpenAPI spec not found at {BASE_SPEC}")
    spec = _load(BASE_SPEC)
    for path in overlay_paths():
        merge_spec(spec, _load(path))
    return spec
