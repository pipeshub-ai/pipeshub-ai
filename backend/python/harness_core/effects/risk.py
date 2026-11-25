"""A tool's risk class (OWD-7, design-plan §4.9): ``read``, ``write_internal``, ``write_external``, ``destructive``
or ``financial``.

The effect class and the sink already say what a call does and where its arguments go; the risk class adds what
neither can: that a call cannot be undone (a delete, a send, a sheet cleared) or moves money. Both come from the tool's
own metadata (a toolset's ``type`` tag, an MCP server's ``destructiveHint`` read through the deleting-word rule),
never from the model. ``financial`` ranks above ``destructive``: a payment cannot be undone either.

The class is metadata for policy and audit; which calls ask stays the approval rules' decision (main #4313: a
toolset's writes run, its destructive tools ask), so classifying a tool here changes no approval by itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal

from harness_core.tools.tags import RISK_TAG_KEY

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.effects.types import ToolEffect

RiskClass = Literal["read", "write_internal", "write_external", "destructive", "financial"]
RISK_CLASSES: Final[tuple[RiskClass, ...]] = ("read", "write_internal", "write_external", "destructive", "financial")
# What a tool's metadata may add above its effect; anything else is no mark.
RiskMark = Literal["destructive", "financial"]
_RANK: Final[dict[RiskClass, int]] = {risk: rank for rank, risk in enumerate(RISK_CLASSES)}


def risk_of(effect: ToolEffect, mark: RiskMark | None = None) -> RiskClass:
    """``read`` for a read; any other call is ``write_external`` when its sink is outside the organization and
    ``write_internal`` otherwise (the run's own workspace, its own sandbox, the user's own space, other people in
    the org), unless ``mark`` says the tool cannot be undone. A read whose request carries data out stays a read:
    the flow rule weighs that (``sends_out``), not the risk class."""
    if mark is not None:
        return mark
    if effect.effect == "read":
        return "read"
    return "write_external" if effect.sink.egress == "external" else "write_internal"


def riskier(a: RiskClass, b: RiskClass) -> RiskClass:
    return a if _RANK[a] >= _RANK[b] else b


_FINANCIAL: Final = frozenset({"financial", "payment", "payments"})
_DESTRUCTIVE: Final = frozenset({"destructive", "delete"})


def risk_mark(tags: Mapping[str, set[str]]) -> RiskMark | None:
    """A tool's mark from its tags (key to lower-cased values): ``risk`` (``RISK_TAG_KEY``: what an MCP tool's kind
    says, deleting words and ``destructiveHint`` included), and a toolset's own ``type`` (``destructive`` on a send or
    clear, #4313; ``delete``) or ``category`` (``payments``)."""
    risk, kind, category = tags.get(RISK_TAG_KEY, set()), tags.get("type", set()), tags.get("category", set())
    if (risk | kind | category) & _FINANCIAL:
        return "financial"
    if (risk | kind) & _DESTRUCTIVE:
        return "destructive"
    return None


__all__ = ["RISK_CLASSES", "RiskClass", "RiskMark", "risk_mark", "risk_of", "riskier"]
