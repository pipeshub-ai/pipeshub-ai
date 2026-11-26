"""Release catalog items for the allow-listed engine versions (01-hld §5.1, `harness_engines/allowlist.json`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.control_plane.catalog.items import Display, EngineDescriptor, ReleaseItem
from harness_engines.release import release_allowlist

if TYPE_CHECKING:
    from collections.abc import Iterable

    from harness_engines.release import EngineRelease


class EnginesCatalogSource:
    kind = "engine"
    catalog_file = "engines.json"

    def __init__(self, releases: Iterable[EngineRelease]) -> None:
        self._releases = tuple(releases)

    def items(self) -> list[ReleaseItem]:
        return [self._item(release) for release in self._releases]

    def _item(self, release: EngineRelease) -> ReleaseItem:
        version = release.version
        return ReleaseItem(
            kind=self.kind,
            name=version.kind,
            version=version.version,
            display=Display(
                name=version.kind,
                description=f"{version.kind} {version.version}",
                categories=("engine",),
            ),
            risk_class="execute",
            descriptor_kind="engine",
            descriptors=(EngineDescriptor(version.kind, version.version, version.binary_digest),),
        )


def release_source() -> EnginesCatalogSource:
    return EnginesCatalogSource(release_allowlist())
