"""The published OpenAPI spec, parsed once per test process.

The file is over 3 MB: the pure-Python YAML parser takes several seconds per parse (tens on a busy CI runner), so a test
that parsed it per call ran past the per-test timeout. Tests read the returned dict and never change it.
"""

import functools
from pathlib import Path

import yaml

SPEC = Path(__file__).resolve().parents[3] / "nodejs" / "apps" / "src" / "modules" / "api-docs" / "pipeshub-openapi.yaml"


@functools.cache
def openapi_spec() -> dict:
    return yaml.load(SPEC.read_text(encoding="utf-8"), Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
