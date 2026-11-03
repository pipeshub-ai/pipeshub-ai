"""The ``authenticatedAs`` link a connector sync leaves for the account it authenticated as.

A connector that can name the source account it reads as links the PipesHub user who
authenticated it to that account at the start of every sync, so that user also resolves
the account's permissions for this one connector. When both carry the same email there
is nothing to link, and none may exist.

The shared test tenants usually authenticate as the harness user's own address, so most
runs check that branch; a tenant whose source account has another address checks the
link itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames

if TYPE_CHECKING:
    from helper.graph_provider import GraphProviderProtocol


async def _authenticator(graph_provider: GraphProviderProtocol, connector_id: str) -> dict[str, Any]:
    """The user the link starts from, resolved as the processor does: ``authenticatedBy``, else the creator."""
    app = await graph_provider.get_document(connector_id, CollectionNames.APPS.value) or {}
    user_id = app.get("authenticatedBy") or app.get("createdBy")
    user = await graph_provider.get_user_by_user_id(user_id) if user_id else None
    assert user, f"connector {connector_id}: the user who authenticated it ({user_id!r}) is not in the graph"
    return user


async def _linked_user_keys(
    graph_provider: GraphProviderProtocol, user_key: str, connector_id: str,
) -> list[str]:
    edges = await graph_provider.get_edges_from_node(
        f"{CollectionNames.USERS.value}/{user_key}", CollectionNames.AUTHENTICATED_AS.value,
    )
    return [
        (e.get("_to") or e.get("to_id") or "").split("/")[-1]
        for e in edges or [] if e.get("connectorId") == connector_id
    ]


async def assert_authenticated_identity_link(
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    email: str | None,
    source_user_id: str | None,
) -> None:
    """Assert the link matches the source account the connector reads as.

    ``email`` and ``source_user_id`` come from the source's own "me" call, normalised the
    way the connector normalises them. With either missing the connector has nothing to
    link, so no link may exist.
    """
    authenticator = await _authenticator(graph_provider, connector_id)
    own_email = (authenticator.get("email") or "").lower()
    linked = await _linked_user_keys(
        graph_provider, authenticator.get("_key") or authenticator.get("id"), connector_id,
    )

    if not email or not source_user_id or email.lower() == own_email:
        assert linked == [], (
            f"connector {connector_id} links {own_email} to {linked}, but the source account "
            f"({email!r}, id {source_user_id!r}) is that user's own or could not be read"
        )
        return

    source_user = await graph_provider.get_user_by_email(email)
    assert source_user is not None, (
        f"no user holds {email}, the account connector {connector_id} authenticated as"
    )
    assert linked == [source_user.id], (
        f"connector {connector_id} should link {own_email} to {email} ({source_user.id}), got {linked}"
    )
