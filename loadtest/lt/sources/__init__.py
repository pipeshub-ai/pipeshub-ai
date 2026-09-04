"""Load-source registry.

Adding a connector to the harness means adding one module here that subclasses
LoadSource and decorates itself with @registry.register, then importing it
below. Nothing in the runner changes.
"""

from . import (  # noqa: F401  (imported to register)
    confluence_source,
    minio_source,
)
from .base import MANUAL_INDEX_OFF, LoadSource, Unit, registry

__all__ = ["LoadSource", "Unit", "registry", "MANUAL_INDEX_OFF", "build"]


def build(name: str, options: dict | None = None) -> LoadSource:
    return registry.get(name)(options or {})
