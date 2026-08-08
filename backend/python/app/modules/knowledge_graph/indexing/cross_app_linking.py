"""Cross-app hard-key entity linking (KG Clean Rebuild plan, Phase 6 /
Part H: "hard-key bridges on connector enable").

Distinct from ``EntityResolutionService`` (Part C): that resolves *within* a
single extraction envelope against vector-store candidates using name
similarity + LLM adjudication. This module links entities *across* apps that
were never in the same envelope at all — e.g. an org member's ``users``
record and a connector-specific external contact both carrying the same
``email`` — using an exact hard-key match only (see
``IGraphDBProvider.find_nodes_by_hard_key``), with no semantic/LLM step,
since a hard key is exact by definition.

Every linked pair gets a bi-temporal ``SAME_AS`` edge (see
``EntityRelations.SAME_AS``) via ``BitemporalGraphWriter`` — so re-running
this after a connector re-sync is idempotent (an unchanged pair no-ops) and
a link that stops holding can later be invalidated without losing history.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config.constants.arangodb import CollectionNames, EntityRelations
from app.modules.knowledge_graph.indexing.temporal import BitemporalGraphWriter, NodeRef

if TYPE_CHECKING:
    import logging

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

# Collections eligible for hard-key linking today: every entry must carry
# an `orgId` field directly (IGraphDBProvider.find_nodes_by_hard_key is an
# org-scoped exact-match filter, not a traversal-derived scope). `people`
# (global, deduplicated by a hash of email across all orgs — see
# app.schema.arango.documents.people_schema) is deliberately excluded until
# a separate org-unaware lookup exists for it.
DEFAULT_HARD_KEY_COLLECTIONS: tuple[str, ...] = (CollectionNames.USERS.value,)

_NODE_ID_FIELDS = ("_key", "id")


def _node_id(node: dict) -> str | None:
    for field in _NODE_ID_FIELDS:
        value = node.get(field)
        if value:
            return str(value)
    return None


class CrossAppEntityLinker:
    """Bridges nodes across connectors/apps that share an exact hard-key
    value with a bi-temporal ``SAME_AS`` edge.
    """

    def __init__(
        self,
        graph_provider: "IGraphDBProvider",
        logger: logging.Logger,
        *,
        hard_key_field: str = "email",
        node_collections: tuple[str, ...] = DEFAULT_HARD_KEY_COLLECTIONS,
        edge_type: str = EntityRelations.SAME_AS.value,
    ) -> None:
        self.graph_provider = graph_provider
        self.logger = logger
        self.hard_key_field = hard_key_field
        self.node_collections = node_collections
        self.edge_type = edge_type
        self.writer = BitemporalGraphWriter(graph_provider, logger)

    async def link_by_hard_key(self, org_id: str, value: str) -> int:
        """Find every node across ``node_collections`` sharing ``value`` on
        ``hard_key_field`` and bi-temporally link every pair.

        Returns the number of pairs written/updated (no-ops from
        ``upsert_bitemporal_edge`` still count — they represent a confirmed,
        still-current link, not a failure).
        """
        if not org_id or not value:
            return 0
        try:
            nodes = await self.graph_provider.find_nodes_by_hard_key(
                org_id, list(self.node_collections), self.hard_key_field, value,
            )
        except Exception as exc:
            self.logger.warning(
                "CrossAppEntityLinker.link_by_hard_key: lookup failed for value=%r: %s", value, exc,
            )
            return 0

        refs: list[NodeRef] = []
        for node in nodes:
            node_id = _node_id(node)
            collection = node.get("_collection")
            if node_id and collection:
                refs.append(NodeRef(node_id, collection))
        # Distinct nodes only — a node's own hard-key lookup can otherwise
        # match itself if two collections alias the same physical document.
        unique_refs = list(dict.fromkeys(refs))
        if len(unique_refs) < 2:
            return 0

        written = 0
        for i in range(len(unique_refs)):
            for j in range(i + 1, len(unique_refs)):
                try:
                    await self.writer.write_edge(
                        org_id, unique_refs[i], unique_refs[j], self.edge_type,
                        attributes={"hardKeyField": self.hard_key_field, "hardKeyValue": value},
                    )
                    written += 1
                except Exception as exc:
                    self.logger.warning(
                        "CrossAppEntityLinker.link_by_hard_key: edge write failed for "
                        "%s <-> %s: %s", unique_refs[i], unique_refs[j], exc,
                    )
        return written

    async def link_batch(self, org_id: str, values: list[str]) -> int:
        """Link many hard-key values in one pass (e.g. every email seen in a
        connector's freshly-synced user batch). Best-effort per value — one
        bad value does not abort the rest of the batch.

        Returns the total number of pairs written/updated across all values.
        """
        total = 0
        for value in values:
            total += await self.link_by_hard_key(org_id, value)
        return total

    async def link_org_users(self, org_id: str, *, max_users: int = 500) -> int:
        """Entry point for "hard-key bridges on connector enable": re-derive
        cross-app links for this org's directly-provisioned users.

        Called once per connector-enable event (see ``EventService._handle_init``).
        At that moment a newly-enabled connector's own external
        contacts/records typically have not synced yet, so this pass mostly
        re-confirms links against whatever other connectors have *already*
        synced — cross-app coverage is expected to build up incrementally as
        more connectors are enabled over time, not completely in one run.
        Safe to call repeatedly: every write goes through
        ``upsert_bitemporal_edge``, which no-ops on an unchanged link.

        ``max_users`` bounds a single run to this org's own directly-owned
        user directory (not external/API data, so no rate-limit concern) —
        set generously, but never unbounded, per this org's own user count.
        """
        if not org_id:
            return 0
        try:
            users = await self.graph_provider.get_users(org_id, active=True)
        except Exception as exc:
            self.logger.warning("CrossAppEntityLinker.link_org_users: get_users failed: %s", exc)
            return 0

        if len(users) > max_users:
            self.logger.warning(
                "link_org_users: org=%s has %d users, truncating to %d for this pass",
                org_id, len(users), max_users,
            )
            users = users[:max_users]

        emails = {
            str(user["email"]).strip().lower()
            for user in users
            if isinstance(user, dict) and user.get("email")
        }
        if not emails:
            return 0
        return await self.link_batch(org_id, sorted(emails))


__all__ = ["CrossAppEntityLinker", "DEFAULT_HARD_KEY_COLLECTIONS"]
