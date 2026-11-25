"""The harness's extension table names the registry's own type for every extension it lists.

``harness_core`` imports nothing from ``app`` (K6), so it cannot derive its table from
``app.config.file_types``; this pin keeps the copy from drifting from the registry instead.
"""
from __future__ import annotations

from app.config import file_types
from harness_core.mime import EXTENSION_TO_MIME


def test_every_harness_extension_maps_to_the_registry_canonical_mime() -> None:
    drift = {}
    for suffix, mime in EXTENSION_TO_MIME.items():
        registered = file_types.by_extension(suffix.lstrip("."))
        canonical = registered.mimes[0] if registered and registered.mimes else None
        if mime != canonical:
            drift[suffix] = (mime, canonical)
    assert drift == {}, f"harness_core.mime disagrees with app.config.file_types: {drift}"
