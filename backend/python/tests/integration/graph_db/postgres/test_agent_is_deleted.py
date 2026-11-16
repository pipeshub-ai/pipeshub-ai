"""An agent or agent template stored without ``isDeleted`` is live on PostgreSQL, as on Neo4j and ArangoDB; one marked deleted is not.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from .test_access import w as w

if TYPE_CHECKING:
    from .test_access import World

pytestmark = pytest.mark.integration


async def test_an_agent_or_template_without_the_deleted_field_is_live_and_one_marked_deleted_is_not(w: World) -> None:
    """A missing ``isDeleted`` is not deleted, as on Neo4j and ArangoDB; ``isDeleted: true`` is."""
    await w.nodes("organizations", {"_key": w.org})
    await w.nodes("agentInstances", {"_key": "bare", "name": "Bare"}, {"_key": "dead", "name": "Dead", "isDeleted": True})
    await w.nodes("agentTemplates", {"_key": "tbare", "name": "Bare"}, {"_key": "tdead", "name": "Dead", "isDeleted": True})
    for coll, key in (("agentInstances", "bare"), ("agentInstances", "dead"), ("agentTemplates", "tbare"), ("agentTemplates", "tdead")):
        await w.edge("permission", ("users", "u"), (coll, key), type="USER", role="OWNER")
    p = w.p
    assert "isDeleted" not in await p.get_document("bare", "agentInstances") and "isDeleted" not in await p.get_document("tbare", "agentTemplates")
    assert (await p.check_agent_permission("bare", "u", w.org))["user_role"] == "OWNER" and await p.check_agent_permission("dead", "u", w.org) is None
    assert (await p.get_agent("bare", w.org))["name"] == "Bare" and await p.get_agent("dead", w.org) is None
    assert (await p.get_template("tbare", "u"))["user_role"] == "OWNER" and await p.get_template("tdead", "u") is None
