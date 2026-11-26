"""The build-time half of ``CatalogSource`` (03-interfaces §3.1): where a kind's release items come from.

Each kind's source lives beside the registry that serves the kind; the catalog core never imports
one. Node's ``CatalogSource.releaseItems`` serves the data these produce.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.control_plane.catalog.items import ReleaseItem


class ReleaseItemSource(Protocol):
    @property
    def kind(self) -> str: ...

    @property
    def catalog_file(self) -> str:
        """The file under ``contracts/control-plane/catalog/`` that holds this kind's items."""
        ...

    def items(self) -> Sequence[ReleaseItem]: ...
