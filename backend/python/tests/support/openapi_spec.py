"""The published OpenAPI spec, parsed once per test process.

The spec is several megabytes of YAML. Parsing it with the pure-Python loader
takes seconds, so a test that re-parsed it for every payload it checked ran into
the per-test timeout on a loaded runner.
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

SPEC_PATH = (
    Path(__file__).resolve().parents[3]
    / "nodejs" / "apps" / "src" / "modules" / "api-docs" / "pipeshub-openapi.yaml"
)

_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


@functools.cache
def published_spec() -> dict[str, Any]:
    """The whole spec. Shared by every caller: treat it as read-only."""
    with SPEC_PATH.open(encoding="utf-8") as handle:
        return yaml.load(handle, Loader=_LOADER)


def published_components() -> dict[str, Any]:
    """The spec's ``components``, for resolving ``#/components/...`` references."""
    return published_spec()["components"]
