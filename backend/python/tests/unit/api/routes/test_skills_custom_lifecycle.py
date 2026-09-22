"""End-to-end lifecycle of a custom skill through the REAL REST route
functions (`app.api.routes.skills`) and a REAL `SkillManager`/
`GraphSkillStore` over a `FakeGraphProvider` — not mocks all the way down,
so a graph-key-encoding or edge-shape bug would actually surface here.

Covers: create -> list (custom + builtin) -> assign to an agent
(`_create_skill_edges`, `app.api.routes.agent`) -> `GET /{name}/usage`
shows the agent -> `DELETE` blocked 409 -> `DELETE?detach=true` removes the
`agentHasSkill` edge and the skill doc; then cross-user (same org) and
cross-org isolation on the same real store.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.middlewares.caller_role import CallerRole, CallerRoleStatus
from app.api.routes.agent import _create_skill_edges
from app.api.routes.skills import (
    DeprecateRequest,
    PatchBodyRequest,
    ResourceWriteRequest,
    RollbackRequest,
    SkillWriteRequest,
    create_skill,
    delete_skill,
    deprecate_skill,
    disable_skill,
    enable_skill,
    get_skill,
    get_skill_usage,
    list_skills,
    patch_skill_body,
    remove_resource,
    rollback_skill,
    update_skill,
    write_resource,
)
from app.config.constants.arangodb import CollectionNames
from tests.unit.agents.adapter.test_skills_graph_store import FakeGraphProvider

_SKILLS = CollectionNames.AGENT_SKILLS.value
_AGENT_HAS_SKILL = CollectionNames.AGENT_HAS_SKILL.value
_AGENT_INSTANCES = CollectionNames.AGENT_INSTANCES.value

_logger = logging.getLogger("test_skills_custom_lifecycle")


class _LifecycleGraphProvider(FakeGraphProvider):
    """Extends the store-level `FakeGraphProvider` with just the two extra
    methods the AGENT-assignment / delete-usage routes need
    (`get_edges_to_node`, `batch_delete_edges`, `get_user_by_user_id`) —
    not needed by `GraphSkillStore`/`GraphUsageTracker` alone, which is why
    the base fake doesn't have them.

    Edge matching is shape-agnostic (`_from`/`_to` OR the documented
    generic `from_id`/`from_collection`/`to_id`/`to_collection`) because
    `_create_skill_edges` (agent.py) writes the former and `delete_skill`
    (skills.py) constructs the latter when asking to delete — a real
    provider normalizes between the two; this fake does too, rather than
    assuming either call site's shape is the only one that exists.
    """

    def __init__(self) -> None:
        super().__init__()
        self._users: dict[str, dict[str, Any]] = {}

    def add_user(self, user_id: str, user_key: str) -> None:
        self._users[user_id] = {"_key": user_key, "userId": user_id}

    async def get_user_by_user_id(self, user_id: str) -> dict[str, Any] | None:
        return self._users.get(user_id)

    @staticmethod
    def _to_ref(edge: dict[str, Any]) -> str | None:
        if edge.get("_to"):
            return edge["_to"]
        if edge.get("to_id"):
            return f"{edge.get('to_collection')}/{edge['to_id']}"
        return None

    @staticmethod
    def _from_ref(edge: dict[str, Any]) -> str | None:
        if edge.get("_from"):
            return edge["_from"]
        if edge.get("from_id"):
            return f"{edge.get('from_collection')}/{edge['from_id']}"
        return None

    async def get_edges_to_node(
        self, node_id: str, edge_collection: str, transaction: str | None = None,
    ) -> list[dict[str, Any]]:
        return [
            dict(e) for e in self._edges.get(edge_collection, [])
            if self._to_ref(e) == node_id
        ]

    async def batch_delete_edges(
        self, edges: list[dict[str, Any]], collection: str, transaction: str | None = None,
    ) -> int:
        targets = {(self._from_ref(e), self._to_ref(e)) for e in edges}
        existing = self._edges.get(collection, [])
        before = len(existing)
        remaining = [e for e in existing if (self._from_ref(e), self._to_ref(e)) not in targets]
        self._edges[collection] = remaining
        return before - len(remaining)


def _request(user_id: str, org_id: str, graph_provider: _LifecycleGraphProvider) -> MagicMock:
    req = MagicMock()
    req.app.container.retrieval_service = AsyncMock(return_value=AsyncMock())
    req.app.container.graph_provider = AsyncMock(return_value=graph_provider)
    req.app.container.logger = MagicMock(return_value=_logger)
    req.app.container.config_service = MagicMock(return_value=MagicMock())
    req.state.user = {"userId": user_id, "orgId": org_id, "email": f"{user_id}@example.com"}
    return req


def _as_role(role: Literal["admin", "member"]):
    """Patch Node's live-role lookup used by builtin enable/disable."""
    return patch(
        "app.api.routes.skills.fetch_caller_role",
        new=AsyncMock(return_value=CallerRole(CallerRoleStatus.VALID, role)),
    )


async def _seed_builtin(req: MagicMock, name: str = "seeded-builtin") -> None:
    # A name that isn't reserved by any real pack under `builtin_packs/` —
    # simulates an already-seeded builtin doc (`source == "builtin"`)
    # without tripping `_reject_if_builtin_name`.
    await create_skill(req, SkillWriteRequest(name=name, description="d", body="body"))
    graph = req.app.container.graph_provider.return_value
    org_id = req.state.user["orgId"]
    graph._col("agentSkills")[f"{org_id}_{name}"]["source"] = "builtin"


_SKILL_MD = """---
name: deploy-runbook
description: Use when deploying a service via the internal runbook
---

Step 1. Run the pre-flight checks.
Step 2. Deploy.
"""


@pytest.fixture
def graph() -> _LifecycleGraphProvider:
    g = _LifecycleGraphProvider()
    g.add_user("user-a", "user-a-key")
    g.add_user("user-b", "user-b-key")
    return g


class TestFullLifecycle:
    async def test_create_list_assign_usage_delete_blocked_then_detached(
        self, graph: _LifecycleGraphProvider,
    ) -> None:
        req = _request("user-a", "org-1", graph)

        # 1. create
        create_resp = await create_skill(
            req, SkillWriteRequest(name="deploy-runbook", description="d", body="body"),
        )
        assert create_resp.status_code == 201

        # 2. list — the custom skill is visible to its own creator
        list_resp = await list_skills(req)
        names = {s["name"] for s in json.loads(list_resp.body)["skills"]}
        assert "deploy-runbook" in names

        # 3. assign to an agent (agent.py's edge-creation helper)
        agent_key = "agent-1"
        await graph.batch_upsert_nodes(
            [{"id": agent_key, "name": "Deploy Bot"}], _AGENT_INSTANCES,
        )
        linked = await _create_skill_edges(
            agent_key, ["deploy-runbook"], "org-1", "user-a-key", graph, _logger,
        )
        assert linked == ["deploy-runbook"]

        # 4. usage shows the agent
        usage_resp = await get_skill_usage(req, "deploy-runbook")
        usage = json.loads(usage_resp.body)
        assert usage["usedByAgents"] == [{"id": agent_key, "name": "Deploy Bot"}]

        # 5. plain delete is blocked 409 while assigned
        with pytest.raises(HTTPException) as exc:
            await delete_skill(req, "deploy-runbook", detach=False)
        assert exc.value.status_code == 409
        assert exc.value.detail["usedByAgents"] == [{"id": agent_key, "name": "Deploy Bot"}]

        # 6. detach=true unassigns and deletes
        delete_resp = await delete_skill(req, "deploy-runbook", detach=True)
        assert delete_resp.status_code == 200
        assert await graph.get_edges_to_node(f"{_SKILLS}/org-1_deploy-runbook", _AGENT_HAS_SKILL) == []
        assert graph._col(_SKILLS).get("org-1_deploy-runbook") is None

    async def test_delete_blocked_when_another_skill_requires_it_even_with_detach(
        self, graph: _LifecycleGraphProvider,
    ) -> None:
        req = _request("user-a", "org-1", graph)
        await create_skill(req, SkillWriteRequest(name="base-skill", description="d", body="body"))
        await create_skill(
            req,
            SkillWriteRequest(
                name="dependent-skill", description="needs base-skill", body="body",
                requires=["base-skill"],
            ),
        )

        with pytest.raises(HTTPException) as exc:
            await delete_skill(req, "base-skill", detach=True)
        assert exc.value.status_code == 409
        assert exc.value.detail["requiredBySkills"] == ["dependent-skill"]


class TestCrossUserAndCrossOrgIsolation:
    async def test_user_b_cannot_update_user_as_skill(self, graph: _LifecycleGraphProvider) -> None:
        req_a = _request("user-a", "org-1", graph)
        req_b = _request("user-b", "org-1", graph)
        await create_skill(req_a, SkillWriteRequest(name="private-skill", description="d", body="body"))

        list_resp_b = await list_skills(req_b)
        names_b = {s["name"] for s in json.loads(list_resp_b.body)["skills"]}
        assert "private-skill" not in names_b

        with pytest.raises(HTTPException) as exc:
            await update_skill(req_b, "private-skill", SkillWriteRequest(description="hijacked", body="new body"))
        assert exc.value.status_code in (404, 409)

    async def test_user_b_cannot_disable_user_as_skill(self, graph: _LifecycleGraphProvider) -> None:
        """Custom-skill mute is creator-only: the management store's
        `visibility_scope` hides user-a's skill from user-b, so disable
        404s the same way GET would — it must not succeed as a co-worker
        or as an org admin acting on someone else's custom skill."""
        req_a = _request("user-a", "org-1", graph)
        req_b = _request("user-b", "org-1", graph)
        await create_skill(req_a, SkillWriteRequest(name="private-skill", description="d", body="body"))

        with pytest.raises(HTTPException) as exc:
            await disable_skill(req_b, "private-skill")
        assert exc.value.status_code == 404

        with _as_role("admin"):
            with pytest.raises(HTTPException) as admin_exc:
                await disable_skill(req_b, "private-skill")
        assert admin_exc.value.status_code == 404

        skill = json.loads((await get_skill(req_a, "private-skill")).body)
        assert skill["status"] == "active"

    async def test_org_b_never_sees_org_as_skill_doc(self, graph: _LifecycleGraphProvider) -> None:
        graph.add_user("user-c", "user-c-key")
        req_org1 = _request("user-a", "org-1", graph)
        req_org2 = _request("user-c", "org-2", graph)
        await create_skill(req_org1, SkillWriteRequest(name="deploy-runbook", description="d", body="body"))

        list_resp_org2 = await list_skills(req_org2)
        names_org2 = {s["name"] for s in json.loads(list_resp_org2.body)["skills"]}
        # Org 2's list seeds its OWN builtin catalog (expected, per-org
        # seeding) but must never see org 1's custom `deploy-runbook` doc.
        assert "deploy-runbook" not in names_org2

        # Org 2 can create a same-named skill independently — different
        # graph doc key (`{orgId}_{name}`), no collision.
        create_resp = await create_skill(
            req_org2, SkillWriteRequest(name="deploy-runbook", description="d2", body="body2"),
        )
        assert create_resp.status_code == 201
        assert graph._col(_SKILLS)["org-1_deploy-runbook"]["orgId"] == "org-1"
        assert graph._col(_SKILLS)["org-2_deploy-runbook"]["orgId"] == "org-2"


class TestEnableDisable:
    async def test_disable_then_enable_round_trip(self, graph: _LifecycleGraphProvider) -> None:
        req = _request("user-a", "org-1", graph)
        await create_skill(req, SkillWriteRequest(name="deploy-runbook", description="d", body="body"))

        disable_resp = await disable_skill(req, "deploy-runbook")
        assert disable_resp.status_code == 200
        assert json.loads(disable_resp.body)["status"] == "disabled"

        # Still visible in the management list — disable mutes agents, not the library.
        names = {s["name"] for s in json.loads((await list_skills(req)).body)["skills"]}
        assert "deploy-runbook" in names

        enable_resp = await enable_skill(req, "deploy-runbook")
        assert enable_resp.status_code == 200
        assert json.loads(enable_resp.body)["status"] == "active"

    async def test_disable_an_already_disabled_skill_is_409(self, graph: _LifecycleGraphProvider) -> None:
        req = _request("user-a", "org-1", graph)
        await create_skill(req, SkillWriteRequest(name="deploy-runbook", description="d", body="body"))
        await disable_skill(req, "deploy-runbook")

        with pytest.raises(HTTPException) as exc:
            await disable_skill(req, "deploy-runbook")
        assert exc.value.status_code == 409

    async def test_enable_an_active_skill_is_409(self, graph: _LifecycleGraphProvider) -> None:
        req = _request("user-a", "org-1", graph)
        await create_skill(req, SkillWriteRequest(name="deploy-runbook", description="d", body="body"))

        with pytest.raises(HTTPException) as exc:
            await enable_skill(req, "deploy-runbook")
        assert exc.value.status_code == 409

    async def test_enable_never_undeprecates(self, graph: _LifecycleGraphProvider) -> None:
        req = _request("user-a", "org-1", graph)
        await create_skill(req, SkillWriteRequest(name="deploy-runbook", description="d", body="body"))
        await deprecate_skill(req, "deploy-runbook", DeprecateRequest(reason="superseded"))

        with pytest.raises(HTTPException) as exc:
            await enable_skill(req, "deploy-runbook")
        assert exc.value.status_code == 409

        skill = json.loads((await get_skill(req, "deploy-runbook")).body)
        assert skill["status"] == "deprecated"

    async def test_disable_unknown_skill_is_404(self, graph: _LifecycleGraphProvider) -> None:
        req = _request("user-a", "org-1", graph)
        with pytest.raises(HTTPException) as exc:
            await disable_skill(req, "nope")
        assert exc.value.status_code == 404


class TestBuiltinSkillsRejectContentMutations:
    """Content mutations of an EXISTING builtin-sourced skill (update/
    patch/rollback/deprecate/delete, resource write/delete) stay 403
    even for an org admin. Enable/disable is the exception — see
    `TestBuiltinAdminAvailability`."""

    async def test_update_rejects_403(self, graph: _LifecycleGraphProvider) -> None:
        req = _request("user-a", "org-1", graph)
        await _seed_builtin(req)
        with _as_role("admin"):
            with pytest.raises(HTTPException) as exc:
                await update_skill(req, "seeded-builtin", SkillWriteRequest(description="hijacked", body="new"))
        assert exc.value.status_code == 403

    async def test_patch_rejects_403(self, graph: _LifecycleGraphProvider) -> None:
        req = _request("user-a", "org-1", graph)
        await _seed_builtin(req)
        with _as_role("admin"):
            with pytest.raises(HTTPException) as exc:
                await patch_skill_body(
                    req, "seeded-builtin", PatchBodyRequest(old_string="body", new_string="hijacked"),
                )
        assert exc.value.status_code == 403

    async def test_rollback_rejects_403(self, graph: _LifecycleGraphProvider) -> None:
        req = _request("user-a", "org-1", graph)
        await _seed_builtin(req)
        with _as_role("admin"):
            with pytest.raises(HTTPException) as exc:
                await rollback_skill(req, "seeded-builtin", RollbackRequest(version="1.0.0"))
        assert exc.value.status_code == 403

    async def test_write_resource_rejects_403(self, graph: _LifecycleGraphProvider) -> None:
        req = _request("user-a", "org-1", graph)
        await _seed_builtin(req)
        with _as_role("admin"):
            with pytest.raises(HTTPException) as exc:
                await write_resource(
                    req, "seeded-builtin", ResourceWriteRequest(path="scripts/hijack.py", content="print(1)"),
                )
        assert exc.value.status_code == 403

    async def test_remove_resource_rejects_403(self, graph: _LifecycleGraphProvider) -> None:
        req = _request("user-a", "org-1", graph)
        await _seed_builtin(req)
        with _as_role("admin"):
            with pytest.raises(HTTPException) as exc:
                await remove_resource(req, "seeded-builtin", path="scripts/hijack.py")
        assert exc.value.status_code == 403

    async def test_deprecate_rejects_403(self, graph: _LifecycleGraphProvider) -> None:
        req = _request("user-a", "org-1", graph)
        await _seed_builtin(req)
        with _as_role("admin"):
            with pytest.raises(HTTPException) as exc:
                await deprecate_skill(req, "seeded-builtin", DeprecateRequest(reason="x"))
        assert exc.value.status_code == 403

    async def test_delete_rejects_403(self, graph: _LifecycleGraphProvider) -> None:
        req = _request("user-a", "org-1", graph)
        await _seed_builtin(req)
        with _as_role("admin"):
            with pytest.raises(HTTPException) as exc:
                await delete_skill(req, "seeded-builtin")
        assert exc.value.status_code == 403


class TestBuiltinAdminAvailability:
    """Builtin enable/disable is org-admin only. Members 403; an admin
    round-trips. Content mutations remain blocked (class above)."""

    async def test_member_disable_rejects_403(self, graph: _LifecycleGraphProvider) -> None:
        req = _request("user-a", "org-1", graph)
        await _seed_builtin(req)
        with _as_role("member"):
            with pytest.raises(HTTPException) as exc:
                await disable_skill(req, "seeded-builtin")
        assert exc.value.status_code == 403

    async def test_member_enable_rejects_403(self, graph: _LifecycleGraphProvider) -> None:
        req = _request("user-a", "org-1", graph)
        await _seed_builtin(req)
        with _as_role("admin"):
            await disable_skill(req, "seeded-builtin")
        with _as_role("member"):
            with pytest.raises(HTTPException) as exc:
                await enable_skill(req, "seeded-builtin")
        assert exc.value.status_code == 403

    async def test_admin_disable_then_enable_round_trip(self, graph: _LifecycleGraphProvider) -> None:
        req = _request("user-a", "org-1", graph)
        await _seed_builtin(req)
        with _as_role("admin"):
            disable_resp = await disable_skill(req, "seeded-builtin")
            assert disable_resp.status_code == 200
            assert json.loads(disable_resp.body)["status"] == "disabled"

            enable_resp = await enable_skill(req, "seeded-builtin")
            assert enable_resp.status_code == 200
            assert json.loads(enable_resp.body)["status"] == "active"

    async def test_any_org_admin_can_mute_a_builtin(self, graph: _LifecycleGraphProvider) -> None:
        """Builtins are org-visible, not creator-scoped — a different
        admin in the same org can mute a skill another identity seeded."""
        req_a = _request("user-a", "org-1", graph)
        req_b = _request("user-b", "org-1", graph)
        await _seed_builtin(req_a)
        with _as_role("admin"):
            disable_resp = await disable_skill(req_b, "seeded-builtin")
        assert disable_resp.status_code == 200
        assert json.loads(disable_resp.body)["status"] == "disabled"


class TestPutPreservesLifecycleStatus:
    """Regression test for the PUT-reactivates bug: `_build_content` used
    to always render `status=ACTIVE, source=MANUAL` on update, so saving a
    disabled or deprecated skill's content silently flipped it back to
    active."""

    async def test_editing_a_disabled_skill_stays_disabled(self, graph: _LifecycleGraphProvider) -> None:
        req = _request("user-a", "org-1", graph)
        await create_skill(req, SkillWriteRequest(name="deploy-runbook", description="d", body="body"))
        await disable_skill(req, "deploy-runbook")

        await update_skill(req, "deploy-runbook", SkillWriteRequest(description="d2", body="new body"))

        skill = json.loads((await get_skill(req, "deploy-runbook")).body)
        assert skill["status"] == "disabled"
        assert skill["body"] == "new body"

    async def test_editing_a_deprecated_skill_preserves_reason_and_replacement(
        self, graph: _LifecycleGraphProvider,
    ) -> None:
        req = _request("user-a", "org-1", graph)
        await create_skill(req, SkillWriteRequest(name="deploy-runbook", description="d", body="body"))
        await create_skill(req, SkillWriteRequest(name="deploy-runbook-v2", description="d", body="body"))
        await deprecate_skill(
            req, "deploy-runbook", DeprecateRequest(reason="superseded", replaced_by="deploy-runbook-v2"),
        )

        await update_skill(req, "deploy-runbook", SkillWriteRequest(description="d2", body="new body"))

        skill = json.loads((await get_skill(req, "deploy-runbook")).body)
        assert skill["status"] == "deprecated"
        assert skill["deprecatedReason"] == "superseded"
        assert skill["replacedBy"] == "deploy-runbook-v2"
