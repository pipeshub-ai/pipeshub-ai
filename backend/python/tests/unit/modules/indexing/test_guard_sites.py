"""The registry the structural guards read: empty as shipped, and an extra root is scanned and its sites are keyed by it."""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING

from tests.support import guard_sites

if TYPE_CHECKING:
    from pathlib import Path


def test_the_default_registry_module_registers_nothing() -> None:
    """The module as shipped, loaded fresh: a build whose edition has installed sites in the live registry does not change this."""
    spec = importlib.util.spec_from_file_location("_guard_sites_as_shipped", guard_sites.__file__)
    assert spec is not None and spec.loader is not None
    shipped = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shipped)

    extension_points = {name: value for name, value in vars(shipped).items() if name.startswith("EXTRA_")}
    assert "EXTRA_PERMISSION_WRITERS" in extension_points and "EXTRA_SOURCE_ROOTS" in extension_points
    assert [name for name, value in extension_points.items() if value] == []
    # an edition may add to the live registry, but only under names the shipped module declares
    assert {name for name in vars(guard_sites) if name.startswith("EXTRA_")} >= set(extension_points)


def test_an_extra_root_is_scanned_and_resolved_by_its_own_key(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "extras"
    root.mkdir()
    (root / "writer.py").write_text("x = 1\n")
    monkeypatch.setattr(guard_sites, "EXTRA_SOURCE_ROOTS", (root,))

    keys = {key for key, _ in guard_sites.source_files()}

    assert "extras/writer.py" in keys
    assert "modules/indexing/run.py" in keys
    assert guard_sites.path_of("extras/writer.py") == root / "writer.py"
    assert guard_sites.path_of("modules/indexing/run.py") == guard_sites.APP / "modules/indexing/run.py"
