"""Which connectors put code in the graph.

The code graph is not a source of its own — it is a view over files some
connector ingested. That makes "is the code graph usable here?" the same
question as "did a repo connector sync anything for this agent?", which is what
this module answers.

Kept beside the projection and edge builder rather than in the tool layer: this
list has to change when a connector starts emitting code files, and that change
originates here, not in the agent.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames, Connectors

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

__all__ = [
    "CODE_CONNECTOR_TYPES",
    "NORMALIZED_CODE_TYPES",
    "agent_knowledge_has_code_connector",
    "connector_instances_have_code",
    "has_code_connector_configured",
    "normalize_connector_type",
]

# Every connector that syncs CODE_FILE records, and so produces the blocks the
# code graph is built from: GitLab and GitLab Personal
# (`connectors/sources/gitlab/`, `gitlab_personal/`) plus both GitHub flavours,
# which are distinct `Connectors` values — a GitHub Teams-only org would
# otherwise have its plainly indexed repo report no code connector.
CODE_CONNECTOR_TYPES = frozenset({
    Connectors.GITLAB.value,
    Connectors.GITLAB_PERSONAL.value,
    Connectors.GITHUB.value,
    Connectors.GITHUB_TEAMS.value,
})


def normalize_connector_type(value: object) -> str:
    """Connector types reach us in three spellings for the same thing.

    The stored App node says `GitLab`, a record says `GITLAB`, and attached
    knowledge says `gitlab`; the multi-word ones vary further —
    `Connectors.GITLAB_PERSONAL` is `"GITLAB PERSONAL"` while agent knowledge
    spells it `gitlab_personal`. Comparing on upper-case alone silently misses
    that pair, so separators are folded too.
    """
    return str(value or "").upper().replace("_", " ").strip()


NORMALIZED_CODE_TYPES = frozenset(normalize_connector_type(t) for t in CODE_CONNECTOR_TYPES)


def connector_instances_have_code(instances: list[dict[str, Any]] | None) -> bool:
    """Repo check over an already-fetched connector-instance list.

    The org-level half of the pair. Both stream entry points already hold the
    list from `fetch_user_connector_instances`, so this reads it instead of
    issuing the same query a third time — see
    :func:`app.utils.execute_query.connector_instances_have_sql`.
    """
    return any(
        normalize_connector_type(i.get("type")) in NORMALIZED_CODE_TYPES and bool(i.get("isConfigured"))
        for i in (instances or [])
    )


async def has_code_connector_configured(
    graph_provider: "IGraphDBProvider",
    user_id: str,
    org_id: str,
) -> bool:
    """`connector_instances_have_code` for a caller holding no instance list.

    Attached knowledge is a stored list, so an agent can still name a connector
    that has since been deleted; this is what catches that. Failure is reported
    as absence, not raised — a tool that quietly does not appear beats a request
    that dies on a connector probe.
    """
    from app.connectors.core.registry.connector_builder import ConnectorScope

    try:
        return connector_instances_have_code(
            await graph_provider.get_user_connector_instances(
                collection=CollectionNames.APPS.value,
                user_id=user_id,
                org_id=org_id,
                team_scope=ConnectorScope.TEAM.value,
                personal_scope=ConnectorScope.PERSONAL.value,
            )
        )
    except Exception as exc:
        logger.warning("Code connector check failed: %s", exc)
        return False


def agent_knowledge_has_code_connector(
    agent_knowledge: list[dict[str, Any]] | None,
) -> bool:
    """Whether this agent has a repo connector attached as knowledge.

    The agent-level half of the pair, mirroring
    `agent_knowledge_has_slack_connector`. The default agent is synthesized with
    every user connector attached, so this is True whenever the org has one. A
    custom agent scoped to, say, Jira only returns False — and should, since it
    has no code to reason about.
    """
    if not agent_knowledge:
        return False
    return any(
        isinstance(entry, dict) and normalize_connector_type(entry.get("type")) in NORMALIZED_CODE_TYPES
        for entry in agent_knowledge
    )
