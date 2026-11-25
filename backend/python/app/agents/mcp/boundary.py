"""Where an MCP server runs, as the org's admin says (SEC-B-02-FU-1, ADR-OWD-09 D-MCP-OPENWORLD 4).

A server's ``openWorldHint`` is its own claim; most servers declare nothing, so a read-only tool is ``external`` and
the flow guard holds it after a private read. An admin who runs the server inside the organization (an internal
wiki, a team tracker) says so on the instance, and its tools' arguments are then kept inside it (sink ``self``).
Only a stored ``True`` counts: a missing key, ``"true"`` or ``1`` is not the admin's choice.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

INSIDE_ORGANIZATION_KEY: Final = "insideOrganization"


def inside_organization(instance: Mapping[str, Any] | None) -> bool:
    return (instance or {}).get(INSIDE_ORGANIZATION_KEY) is True


__all__ = ["INSIDE_ORGANIZATION_KEY", "inside_organization"]
