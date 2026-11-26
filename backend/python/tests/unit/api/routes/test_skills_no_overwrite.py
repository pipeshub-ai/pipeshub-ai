"""Through the real route functions: names are org-unique, builtins are the seeder's, admins moderate."""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import HTTPException

from app.api.routes.skills import (
    DeprecateRequest,
    FinalizeImportRequest,
    ResourceWriteRequest,
    RollbackRequest,
    SkillWriteRequest,
    create_skill,
    delete_skill,
    deprecate_skill,
    disable_skill,
    enable_skill,
    finalize_import,
    get_skill,
    list_org_skills,
    list_skills,
    remove_resource,
    rollback_skill,
    update_skill,
    write_resource,
)
from tests.support.bound_org import another_request, bind_test_org
from tests.unit.agents.adapter.test_skills_no_overwrite import _ReCreatedBeforeTheWrite
from tests.unit.api.routes.test_skills_custom_lifecycle import (
    _as_role,
    _LifecycleGraphProvider,
    _request,
    _seed_builtin,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from unittest.mock import MagicMock


@pytest.fixture(autouse=True)
def bound_org() -> Iterator[str]:
    yield from bind_test_org("org-1")


_PRIVATE_BODY = "Private instructions of the first author"

_CLAIMING_MD = """---
name: imported-skill
description: Use when importing
metadata:
  agent-loop:
    source: builtin
    pack_name: imported-skill
    pack_version: 9.9.9
---

Imported body.
"""


@pytest.fixture
def graph() -> _LifecycleGraphProvider:
    g = _LifecycleGraphProvider()
    g.add_user("user-a", "user-a-key")
    g.add_user("user-b", "user-b-key")
    g.add_user("user-c", "user-c-key")
    return g


def _state(graph: _LifecycleGraphProvider) -> dict[str, Any]:
    return copy.deepcopy({"nodes": graph._collections, "edges": graph._edges})


async def _first_author(graph: _LifecycleGraphProvider) -> MagicMock:
    req = _request("user-a", "org-1", graph)
    await create_skill(req, SkillWriteRequest(
        name="deploy-runbook", description="first", body=_PRIVATE_BODY, requires=["base-skill"],
    ))
    return req


async def test_create_with_a_co_workers_name_is_409_and_leaks_nothing(graph) -> None:
    req_a = await _first_author(graph)
    before = _state(graph)

    with pytest.raises(HTTPException) as exc:
        await create_skill(
            _request("user-b", "org-1", graph),
            SkillWriteRequest(name="deploy-runbook", description="second", body="a different body"),
        )

    assert exc.value.status_code == 409
    detail = json.dumps(exc.value.detail)
    assert "user-a" not in detail and _PRIVATE_BODY not in detail
    assert _state(graph) == before
    assert json.loads((await get_skill(req_a, "deploy-runbook")).body)["description"] == "first"


async def test_import_with_a_co_workers_name_is_409(graph) -> None:
    await _first_author(graph)
    before = _state(graph)
    content = "---\nname: deploy-runbook\ndescription: second\n---\n\na different body\n"

    with pytest.raises(HTTPException) as exc:
        await finalize_import(
            _request("user-b", "org-1", graph),
            FinalizeImportRequest(content=content, resources={"x.md": "added"}),
        )

    assert exc.value.status_code == 409
    assert _state(graph) == before


async def test_owner_still_creates_updates_and_deletes(graph) -> None:
    req_a = await _first_author(graph)
    assert (await update_skill(req_a, "deploy-runbook", SkillWriteRequest(description="v2", body="b2"))).status_code == 200
    assert "deploy-runbook" in {s["name"] for s in json.loads((await list_skills(req_a)).body)["skills"]}
    assert (await delete_skill(req_a, "deploy-runbook", detach=False)).status_code == 200


async def test_the_other_user_can_create_a_different_name(graph) -> None:
    await _first_author(graph)
    resp = await create_skill(
        _request("user-b", "org-1", graph), SkillWriteRequest(name="my-own", description="d", body="b"),
    )
    assert resp.status_code == 201


class TestAMemberCannotStoreABuiltin:
    async def test_an_import_that_says_builtin_is_stored_as_an_ordinary_skill(self, graph) -> None:
        req_b = _request("user-b", "org-1", graph)
        await finalize_import(req_b, FinalizeImportRequest(content=_CLAIMING_MD))

        doc = graph._col("agentSkills")["org-1_imported-skill"]
        assert doc["source"] == "imported" and doc["packName"] is None and doc["packVersion"] is None
        assert "builtin" not in doc["content"]

    async def test_a_co_worker_does_not_see_it(self, graph) -> None:
        await finalize_import(_request("user-b", "org-1", graph), FinalizeImportRequest(content=_CLAIMING_MD))
        listed = json.loads((await list_skills(_request("user-c", "org-1", graph))).body)["skills"]
        assert listed == []


class TestAdminModeration:
    async def test_an_admin_can_disable_enable_and_delete_a_members_skill(self, graph) -> None:
        req_a = await _first_author(graph)
        req_admin = _request("user-b", "org-1", graph)

        with _as_role("admin"):
            assert (await disable_skill(req_admin, "deploy-runbook")).status_code == 200
            assert json.loads((await get_skill(req_a, "deploy-runbook")).body)["status"] == "disabled"
            assert (await enable_skill(req_admin, "deploy-runbook")).status_code == 200
            assert (await delete_skill(req_admin, "deploy-runbook", detach=False)).status_code == 200
        assert "org-1_deploy-runbook" not in graph._col("agentSkills")

    async def test_a_member_still_gets_404_for_a_co_workers_skill(self, graph) -> None:
        await _first_author(graph)
        req_b = _request("user-b", "org-1", graph)
        before = _state(graph)

        with _as_role("member"):
            for call in (
                lambda: disable_skill(req_b, "deploy-runbook"),
                lambda: delete_skill(req_b, "deploy-runbook", detach=False),
            ):
                with pytest.raises(HTTPException) as exc:
                    await call()
                assert exc.value.status_code == 404
        assert _state(graph) == before

    async def test_an_admin_can_remove_a_skill_a_member_stored_as_a_builtin(self, graph) -> None:
        """Skills stored before the builtin claim was dropped: they say `builtin`, but no seeder wrote them."""
        await finalize_import(_request("user-b", "org-1", graph), FinalizeImportRequest(content=_CLAIMING_MD))
        graph._col("agentSkills")["org-1_imported-skill"]["source"] = "builtin"

        with _as_role("admin"):
            assert (await disable_skill(_request("user-a", "org-1", graph), "imported-skill")).status_code == 200
            assert (await delete_skill(_request("user-a", "org-1", graph), "imported-skill", detach=False)).status_code == 200
        assert "org-1_imported-skill" not in graph._col("agentSkills")

    async def test_a_seeded_builtin_still_cannot_be_deleted_even_by_an_admin(self, graph) -> None:
        req = _request("user-a", "org-1", graph)
        await _seed_builtin(req)
        with _as_role("admin"):
            with pytest.raises(HTTPException) as exc:
                await delete_skill(req, "seeded-builtin", detach=False)
        assert exc.value.status_code == 403


class TestAdminListing:
    async def test_an_admin_lists_every_skill_in_the_org_without_bodies(self, graph) -> None:
        await _first_author(graph)
        await finalize_import(_request("user-b", "org-1", graph), FinalizeImportRequest(content=_CLAIMING_MD))
        graph._col("agentSkills")["org-1_imported-skill"]["source"] = "builtin"

        with _as_role("admin"):
            body = json.loads((await list_org_skills(_request("user-c", "org-1", graph))).body)

        assert {s["name"] for s in body["skills"]} == {"deploy-runbook", "imported-skill"}
        assert all("body" not in s for s in body["skills"])

    async def test_a_member_cannot_list_them(self, graph) -> None:
        await _first_author(graph)
        with _as_role("member"):
            with pytest.raises(HTTPException) as exc:
                await list_org_skills(_request("user-b", "org-1", graph))
        assert exc.value.status_code == 403

    async def test_an_admin_sees_only_their_own_org(self, graph) -> None:
        await _first_author(graph)
        graph.add_user("user-z", "user-z-key")
        with _as_role("admin"), another_request("org-2"):
            body = json.loads((await list_org_skills(_request("user-z", "org-2", graph))).body)
        assert body["skills"] == []

    async def test_an_unknown_status_or_source_filter_is_a_400_not_a_server_error(self, graph) -> None:
        await _first_author(graph)
        for kwargs in ({"status": "nope"}, {"source": "bogus"}):
            with _as_role("admin"), pytest.raises(HTTPException) as exc:
                await list_org_skills(_request("user-c", "org-1", graph), **kwargs)
            assert exc.value.status_code == 400
            with pytest.raises(HTTPException) as exc:
                await list_skills(_request("user-b", "org-1", graph), **kwargs)
            assert exc.value.status_code == 400

    async def test_the_listing_route_is_not_shadowed_by_a_skill_name(self) -> None:
        from app.api.routes.skills import router

        paths = [r.path for r in router.routes if "GET" in getattr(r, "methods", ())]
        assert paths.index("/admin/skills") < paths.index("/{name}")


class _ReCreatedRouteGraph(_ReCreatedBeforeTheWrite, _LifecycleGraphProvider):
    """The route fake, with the name deleted and created again by user-b right before the store's write."""


class TestAWriteThatMissesIsAConflict:
    """A write bound to the doc the route's store read, which no longer holds the name, is
    a 409 with nothing about the new skill, never a success and never a 500."""

    @pytest.mark.parametrize(
        "call",
        [
            pytest.param(lambda r: write_resource(r, "deploy-service", ResourceWriteRequest(path="n.md", content="x")),
                         id="write-resource"),
            pytest.param(lambda r: remove_resource(r, "deploy-service", path="a.md"), id="remove-resource"),
            pytest.param(lambda r: delete_skill(r, "deploy-service", detach=False), id="delete"),
            pytest.param(lambda r: delete_skill(r, "deploy-service", detach=True), id="delete-detach"),
            pytest.param(lambda r: rollback_skill(r, "deploy-service", RollbackRequest(version="1.0.0")), id="rollback"),
            pytest.param(lambda r: deprecate_skill(r, "deploy-service", DeprecateRequest(reason="old")), id="deprecate"),
            pytest.param(lambda r: update_skill(r, "deploy-service", SkillWriteRequest(
                name="deploy-service", description="Use when deploying", body="Changed body.")), id="update"),
        ],
    )
    async def test_the_route_answers_409_and_the_new_skill_is_untouched(self, call, request) -> None:
        graph = _ReCreatedRouteGraph()
        graph.add_user("user-a", "user-a-key")
        req = _request("user-a", "org-1", graph)
        await create_skill(req, SkillWriteRequest(name="deploy-service", description="Use when deploying", body="First body."))
        await update_skill(req, "deploy-service", SkillWriteRequest(
            name="deploy-service", description="Use when deploying", body="Second body."))
        await write_resource(req, "deploy-service", ResourceWriteRequest(path="a.md", content="from a"))
        if "detach" in request.node.callspec.id:
            graph._edges.setdefault("agentHasSkill", []).append(
                {"_from": "agentInstances/agent-a", "_to": "agentSkills/org-1_deploy-service"},
            )
            graph._col("agentInstances")["agent-a"] = {"id": "agent-a", "name": "Agent A"}
        graph.armed = True

        with pytest.raises(HTTPException) as exc:
            await call(req)
        assert exc.value.status_code == 409
        assert exc.value.detail["currentUpdatedAt"] == 0 and exc.value.detail["currentVersion"] == ""

        doc = graph._col("agentSkills")["org-1_deploy-service"]
        assert doc["createdBy"] == "user-b" and "New owner body" in doc["content"]
        assert doc["resourcePaths"] == ["b.md"] and doc["status"] == "active"
        assert [e["from_id"] for e in graph._edges["agentHasSkill"]] == ["agent-b"]
