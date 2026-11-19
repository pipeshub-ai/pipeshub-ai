"""Where the content behind a search hit lives, and how a hit is shown.

The default, ``SingleHolderLocator``, serves records whose content is held in one stored record, so every method is the
identity. A package that holds one record's content in several stored records installs a factory with
:func:`set_locator_factory`. One locator is built per search, from the graph the caller passes in, so a factory must
never keep a provider.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class ContentLocator(Protocol):
    internal_payload_keys: tuple[str, ...]

    def stamp_hit(self, metadata: dict) -> dict: ...

    async def filter_live(self, hits: list) -> list: ...

    async def owners_of(self, vrids: object) -> dict[str, str]: ...

    async def holds_several(self, vrids: object) -> bool: ...

    def is_address(self, index: int) -> bool: ...

    def public_index(self, holder_index: int | None, local: int) -> int: ...

    def decode_ref(self, address: object, record_id: str | None, holders: object) -> tuple[int, int | None]: ...

    def display_number(self, record: dict, local: int) -> int | None: ...

    async def describe(self, record: object, identity_vrid: str | None, holder_index: int | None) -> None: ...


class SingleHolderLocator:
    internal_payload_keys: tuple[str, ...] = ()

    def stamp_hit(self, metadata: dict) -> dict:
        return metadata

    async def filter_live(self, hits: list) -> list:
        return hits

    async def owners_of(self, vrids: object) -> dict[str, str]:
        return {}

    async def holds_several(self, vrids: object) -> bool:
        return False

    def is_address(self, index: int) -> bool:
        return False

    def public_index(self, holder_index: int | None, local: int) -> int:
        return local

    def decode_ref(self, address: object, record_id: str | None, holders: object) -> tuple[int, int | None]:
        return 0, address

    def display_number(self, record: dict, local: int) -> int | None:
        return local + 1

    async def describe(self, record: object, identity_vrid: str | None, holder_index: int | None) -> None:
        return None


LocatorFactory = Callable[[Any], ContentLocator]


def _single_holder(graph: object) -> ContentLocator:
    return SingleHolderLocator()


_factory: list[LocatorFactory] = [_single_holder]


def set_locator_factory(factory: LocatorFactory) -> None:
    """Install the factory. Installing the one already installed is a no-op."""
    _factory[0] = factory


def reset_locator_factory() -> None:
    _factory[0] = _single_holder


def locator_for(graph: object) -> ContentLocator:
    """A new locator for one search, built from ``graph``."""
    return _factory[0](graph)
