"""The ports an edition supplies to the harness: an extension point, returned by ``app.edition_services.harness_edition``.

A feature that an edition may replace defines its port (a class or a ``Protocol``) and a working default beside it,
and reads the port with ``harness_edition().port(ThePort, DEFAULT)``. A feature this build does not have reads its port
with ``harness_edition().supplied(ThePort)``: ``None`` means the feature is off. This build's edition supplies no
port, so every read answers its default (or ``None``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class HarnessEdition:
    """Each supplied port, by the type it implements."""

    ports: Mapping[type[object], object] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        for kind, port in self.ports.items():
            if port is None:
                raise TypeError(f"the edition supplies None for {kind.__qualname__}: leave it out to keep the default")
            try:
                fits = isinstance(port, kind)
            except TypeError:
                # A Protocol without @runtime_checkable cannot be checked at run time; its type checker checks it.
                continue
            if not fits:
                raise TypeError(f"the edition's {kind.__qualname__} is a {type(port).__qualname__}, which does not implement it")
        object.__setattr__(self, "ports", MappingProxyType(dict(self.ports)))

    def port[T](self, kind: type[T], default: T) -> T:
        """The edition's ``kind``, or ``default`` when it supplies none."""
        supplied = self.ports.get(kind)
        return default if supplied is None else cast("T", supplied)

    def supplied[T](self, kind: type[T]) -> T | None:
        """The edition's ``kind``, or ``None`` when it supplies none (the feature it serves is off)."""
        supplied = self.ports.get(kind)
        return None if supplied is None else cast("T", supplied)


NO_HARNESS_EDITION = HarnessEdition()
