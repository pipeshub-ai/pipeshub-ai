"""Model-call placement outside the agent harness (MG-15, C3.68): chat, the Slack bot, search, indexing and
extraction ask ``model_placement(config_service).verdict`` before they build any model client (``app/utils/llm.py``),
so nothing is sent to a model a placement rule refuses.

Where those rules come from is an extension point, ``ModelPlacementPort``, read through ``harness_edition().port``.
This build's default allows every call.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Protocol

from harness_core.models.placement import NO_MODEL_REGIONS, ModelRegions

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True)
class Verdict:
    """Where a model's calls are served (``None``: not known), why a placement rule refuses them (``None``: it
    allows them), and the error answer and run ending the rule names for its refusal (``None``: none of its own)."""

    region: str | None
    refusal: str | None
    code: str | None = None


class ModelVerdicts(Protocol):
    async def verdict(
        self, provider: str, config: Mapping[str, Any], model_name: str | None = None, *, org_id: str | None = None
    ) -> Verdict:
        """Whether a call to ``model_name`` of this configured entry may go out for the bound org (or ``org_id``).
        Raises ``AccessPolicyUnavailable`` when the rules cannot be read: an unread rule never allows a call."""
        ...


class ModelPlacementPort(ABC):
    @abstractmethod
    def reader(self, config_service: Any) -> ModelVerdicts:  # noqa: ANN401 - the process's ConfigurationService
        """The process's verdicts over ``config_service``."""


class _AllowEvery:
    async def verdict(
        self, provider: str, config: Mapping[str, Any], model_name: str | None = None, *, org_id: str | None = None
    ) -> Verdict:
        return Verdict(None, None)


class AllowEveryModel(ModelPlacementPort):
    """No placement rule: every call may go out."""

    _verdicts: Final = _AllowEvery()

    def reader(self, config_service: Any) -> ModelVerdicts:  # noqa: ANN401
        return self._verdicts


ALLOW_EVERY_MODEL: Final = AllowEveryModel()


def model_placement(config_service: Any) -> ModelVerdicts:  # noqa: ANN401 - the process's ConfigurationService
    """The process's model placement verdicts over ``config_service``."""
    from app.edition_services import harness_edition

    return harness_edition().port(ModelPlacementPort, ALLOW_EVERY_MODEL).reader(config_service)


def model_regions() -> ModelRegions:
    """Where this process reads a model route's region and an org's region pins from (``ModelRegions``); this
    build's default reads no region and holds no pin."""
    from app.edition_services import harness_edition

    return harness_edition().port(ModelRegions, NO_MODEL_REGIONS)


__all__ = ["ALLOW_EVERY_MODEL", "AllowEveryModel", "ModelPlacementPort", "ModelVerdicts", "Verdict", "model_regions", "model_placement"]
