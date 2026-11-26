"""QA-12 (CG-12, TSC-G5): the credential migration's owner lookup on a real Neo4j and a real ArangoDB.

``GraphOwnerOrgs`` decides which org a toolset or MCP credential is copied into (SEC5): a user by its ``userId``, an
agent by its creator's (ArangoDB's agent schema has no ``orgId``, so that is the rule both run). A wrong answer copies a credential into another org; ``None`` holds
it back. Both providers must answer the same, and the migration over them must copy exactly what the fake graph
copies (``test_binding_migration.py``).

Requires the servers of ``test_agent_engine_profile.py`` (``NEO4J_TEST_URI`` / ``ARANGO_TEST_URL``); skipped when
absent. Run: pytest tests/integration/graph_db/test_qa12_credential_owner_orgs.py -m integration
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
from app.harness.credentials.bindings import BindingDocuments
from app.harness.credentials.migration import CredentialBindingMigration, GraphOwnerOrgs
from app.harness.credentials.vault import VaultSecretStore
from harness_core.testing.clock import ManualClock
from tests.integration.graph_db.test_agent_engine_profile import (  # noqa: F401 - fixtures
    arango_provider,
    neo4j_provider,
    provider,
)
from tests.unit.harness.credentials.binding_support import FakeBindingWriter
from tests.unit.harness.credentials.support import DictConfig
from tests.unit.harness.credentials.test_binding_migration import toolset_record

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

USERS = CollectionNames.USERS.value
AGENTS = CollectionNames.AGENT_INSTANCES.value


def _agent(key: str, creator: str) -> dict[str, object]:
    return {
        "_key": key, "name": key, "description": "d", "startMessage": "s", "systemPrompt": "p", "models": [],
        "createdBy": creator, "createdAtTimestamp": 1, "updatedAtTimestamp": 1, "isDeleted": False,
    }


async def test_qa12_10_owner_orgs_and_the_migration_agree_on_both_backends(provider: IGraphDBProvider) -> None:  # noqa: F811
    run = uuid.uuid4().hex[:8]
    org, other = f"org-{run}", f"other-{run}"
    alice, bob, carol = f"alice-{run}", f"bob-{run}", f"carol-{run}"
    user_keys = [f"k-{alice}", f"k-{bob}"]
    agent_other, agent_creator = f"agent-other-{run}", f"agent-creator-{run}"
    await provider.batch_upsert_nodes(
        [
            {"_key": user_keys[0], "userId": alice, "email": f"{alice}@example.test", "orgId": org},
            {"_key": user_keys[1], "userId": bob, "email": f"{bob}@example.test", "orgId": other},
        ],
        USERS,
    )
    await provider.batch_upsert_nodes([_agent(agent_other, user_keys[1]), _agent(agent_creator, user_keys[0])], AGENTS)
    try:
        owners = GraphOwnerOrgs(provider)
        assert await owners.org_of("user", alice) == org
        assert await owners.org_of("user", bob) == other
        assert await owners.org_of("user", carol) is None
        assert await owners.org_of("user", user_keys[0]) is None, "a user is found by its userId, not its key"
        assert await owners.org_of("agent", agent_other) == other
        assert await owners.org_of("agent", agent_creator) == org
        assert await owners.org_of("agent", f"missing-{run}") is None

        config = DictConfig(
            {
                "/services/toolset-instances": [{"_id": "ts-1", "orgId": org, "toolsetType": "slack"}],
                f"/services/toolsets/ts-1/{alice}": toolset_record(),
                f"/services/toolsets/ts-1/{bob}": toolset_record(),
                f"/services/toolsets/ts-1/{carol}": toolset_record(),
                f"/services/toolsets/ts-1/{agent_creator}": toolset_record(agent=agent_creator),
                f"/services/toolsets/ts-1/{agent_other}": toolset_record(agent=agent_other),
            }
        )
        writer = FakeBindingWriter(config)
        migration = CredentialBindingMigration(
            config, owners, writer, BindingDocuments(VaultSecretStore(config, ManualClock())), now_ms=lambda: 1
        )
        report = await migration.apply()
        assert sorted((e["orgId"], e["owner"]) for e in report.copied) == sorted(
            [(org, f"user:{alice}"), (org, f"agent:{agent_creator}")]
        )
        assert {(e["owner"], e["reason"]) for e in report.held} == {
            (f"user:{bob}", "not_visible"),
            (f"agent:{agent_other}", "not_visible"),
            (f"user:{carol}", "owner_unknown"),
        }
        assert {row["orgId"] for row in writer.rows.values()} == {org}
    finally:
        await provider.delete_nodes([agent_other, agent_creator], AGENTS)
        await provider.delete_nodes(user_keys, USERS)
