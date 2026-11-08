"""Which addresses a connector may dial, by the scope it was created with."""

from __future__ import annotations

from app.connectors.core.registry.connector_builder import ConnectorScope


def public_only_for_scope(scope: object) -> bool:
    """Whether a connector of this scope may reach public addresses only.

    A personal connector can be created by any user, so it never reaches private-network
    addresses. A team connector is created by an admin and follows the deployment setting
    (``PIPESHUB_BLOCK_PRIVATE_ADDRESSES``). Anything else, including a missing scope,
    gets the stricter rule.
    """
    return str(getattr(scope, "value", scope)) != ConnectorScope.TEAM.value
