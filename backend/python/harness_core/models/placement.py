"""Where a model route's calls are served, and whether an org's region pins allow it: an extension point,
``ModelRegions``.

A route's region is read from the configuration the admin gave the model; a pin is what an org's narrowing and a
policy layer's ``models`` keep under the members the port names (``pin_fields``); the model gateway's guard asks
``refusal`` before it resolves a route under a pin. This build's default reads no region, names no member, holds no pin
and refuses nothing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Final, override

from harness_core.access.errors import ModelAccessRefused, ModelNotAllowed

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


class PinInvalid(ValueError):
    reason_code = "region_pin_invalid"


class ModelRegions(ABC):
    # The members of an org's narrowing and of a layer's ``models`` that hold a pin: none by default.
    pin_fields: frozenset[str] = frozenset()

    def pin_of(self, stored: Mapping[str, object]) -> frozenset[str]:
        """The pin ``stored`` (an org's narrowing, or a layer's ``models``) holds under ``pin_fields``; empty: none.
        Raises ``PinInvalid`` for one that cannot be read."""
        return frozenset().union(*(self.pin(stored[name]) for name in sorted(self.pin_fields) if name in stored))

    @abstractmethod
    def region_of(
        self, provider: str, configuration: Mapping[str, object] | None, model: str, *, deployment: str
    ) -> str | None:
        """Where a call to ``model`` through this configured entry is served; ``None``: unknown. ``deployment``: the
        region this process runs in."""

    @abstractmethod
    def pin(self, raw: object) -> frozenset[str]:
        """A stored pin (empty: none). Raises ``PinInvalid`` for one that cannot be read."""

    @abstractmethod
    def refusal(self, pins: Iterable[frozenset[str]], region: str | None, *, declared: str | None = None) -> str | None:
        """Why a route in ``region`` may not be called under ``pins``, in plain words; ``None``: it may.
        ``declared``: the region the org declared for the model, for a route whose configuration names none."""

    def refused(self, why: str) -> ModelAccessRefused:
        """The refusal the model gateway's guard raises for a route ``refusal`` names ``why`` for."""
        return ModelNotAllowed(why)


class NoModelRegions(ModelRegions):
    """No region is read and no pin is held: every route may be called."""

    @override
    def region_of(
        self, provider: str, configuration: Mapping[str, object] | None, model: str, *, deployment: str
    ) -> str | None:
        return None

    @override
    def pin(self, raw: object) -> frozenset[str]:
        return frozenset()

    @override
    def refusal(self, pins: Iterable[frozenset[str]], region: str | None, *, declared: str | None = None) -> str | None:
        return None


NO_MODEL_REGIONS: Final = NoModelRegions()


__all__ = ["NO_MODEL_REGIONS", "ModelRegions", "NoModelRegions", "PinInvalid"]
