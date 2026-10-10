"""Contract details the other v2 modules prove only by agreement, or not at all.

* **`parentType` values.** The cross-engine parity tests compare it
  field-for-field, which proves the two engines *agree* — not that either is
  right. Both emitting null passes parity, and every browse row comes back
  nameless.
* **`only_containers` and `record_group_ids`** are otherwise proven only at
  builder level (the generated text contains the right clause), never against a
  store. The sidebar sends `only_containers` on every expand.
* **Available filters** are otherwise unit-tested against a mocked provider, so
  nothing shows the service's gate composing with the real listing query.
"""

import logging

import pytest

from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)

pytestmark = pytest.mark.integration

USER = "user-u"
ORG = "org-1"
GRANTEES = ["user-u", "group-g", "role-r", "team-t", "orgnode-1"]
GATED_APPS = [
    "ex1-app", "ex2-app", "dec-app", "dec-rgl-app", "ex-app",
    "swm-app", "pl-app", "gp-app", "flag-app", "kb-1", "deep-app",
]


@pytest.fixture(params=["neo4j", "arango"])
def provider(request, neo4j_provider, arango_provider):
    return neo4j_provider if request.param == "neo4j" else arango_provider


def _rows(result) -> dict[str, dict]:
    assert len(result["partitions"]) == 1, result["partitions"]
    return {row["id"]: row for row in result["partitions"][0]["rows"]}


# ------------------------------------------------------------- the parent triple

# ------------------------------------------------------------------- filters

# ---------------------------------------------------------- available filters

async def test_available_filters_list_exactly_the_openable_sources(
    loaded_graph, provider
) -> None:
    """End to end: the gate, the listing and the filter list agree.

    `gate-app` (nothing reaches this user) and `kb-2` (a collection with no
    grant) must be excluded, and a reachable collection must be listed.
    """
    service = KnowledgeHubService(
        logger=logging.getLogger("kh_contract"), graph_provider=provider
    )
    filters = await service._get_available_filters(USER, ORG)

    listed = {option.id: option for option in filters.connectors}
    assert set(listed) == set(GATED_APPS), sorted(set(listed) ^ set(GATED_APPS))
    assert "gate-app" not in listed and "kb-2" not in listed
    assert listed["kb-1"].label == "My Collection", listed["kb-1"]
    assert listed["pl-app"].label, "a source with no label is unusable in a filter"
