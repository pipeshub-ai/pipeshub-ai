"""Extended tests for app.api.routes.skills — covers lines 207-641."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.validator import SkillFormatError
from app.api.routes.skills import (
    DeprecateRequest,
    FinalizeImportRequest,
    NpmImportRequest,
    PatchBodyRequest,
    ResourceWriteRequest,
    RollbackRequest,
    SkillWriteRequest,
    UrlImportRequest,
    _edge_source_key,
    _get_user_context,
    _get_user_key,
    _handle_format_error,
    _handle_registry_error,
)

MODULE = "app.api.routes.skills"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_metadata():
    m = MagicMock()
    m.name = "test-skill"
    m.description = "A test skill"
    m.version = "1.0.0"
    m.category = "general"
    m.subcategory = None
    m.tags = ["test"]
    m.status.value = "active"
    m.source.value = "manual"
    m.license = None
    m.compatibility = None
    m.allowed_tools = None
    m.related = []
    m.requires = []
    m.concepts = []
    m.deprecated_reason = None
    m.replaced_by = None
    m.created_at = "2024-01-01"
    m.updated_at = "2024-01-01"
    m.pack_name = None
    m.pack_version = None
    return m


def _mock_skill():
    skill = MagicMock()
    skill.metadata = _mock_metadata()
    skill.body = "# Test body"
    skill.resources = {}
    return skill


def _mock_version_entry(version="1.0.0"):
    v = MagicMock()
    v.version = version
    v.updated_by = "user1"
    v.created_at = "2024-01-01"
    v.summary = "initial"
    return v


def _mock_request():
    req = MagicMock()
    container = MagicMock()
    container.logger.return_value = MagicMock()
    retrieval_svc = AsyncMock()
    container.retrieval_service = AsyncMock(return_value=retrieval_svc)
    graph_provider = AsyncMock()
    container.graph_provider = AsyncMock(return_value=graph_provider)
    req.app.container = container
    req.state.user = {"userId": "u1", "orgId": "o1", "email": "test@example.com"}
    return req


def _mock_manager_and_ctx():
    manager = AsyncMock()
    ctx = {
        "retrieval_service": AsyncMock(),
        "graph_provider": AsyncMock(),
        "logger": MagicMock(),
        "userId": "u1",
        "orgId": "o1",
        "email": "test@example.com",
        "domain": "example.com",
        "userKey": "uk1",
    }
    return manager, ctx


# ============================================================================
# _get_user_context
# ============================================================================

class TestGetUserContext:
    def test_success(self):
        req = _mock_request()
        ctx = _get_user_context(req)
        assert ctx["userId"] == "u1"
        assert ctx["orgId"] == "o1"

    def test_missing_user_id_raises_401(self):
        req = _mock_request()
        req.state.user = {"orgId": "o1"}
        with pytest.raises(HTTPException) as exc:
            _get_user_context(req)
        assert exc.value.status_code == 401

    def test_missing_org_id_raises_401(self):
        req = _mock_request()
        req.state.user = {"userId": "u1"}
        with pytest.raises(HTTPException) as exc:
            _get_user_context(req)
        assert exc.value.status_code == 401

    def test_no_user_attr_raises_401(self):
        req = _mock_request()
        req.state.user = {}
        with pytest.raises(HTTPException) as exc:
            _get_user_context(req)
        assert exc.value.status_code == 401


# ============================================================================
# _get_user_key
# ============================================================================

class TestGetUserKey:
    @pytest.mark.asyncio
    async def test_success(self):
        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1", "name": "Test"})
        logger = MagicMock()
        result = await _get_user_key("u1", gp, logger)
        assert result == "uk1"

    @pytest.mark.asyncio
    async def test_user_not_found_raises_404(self):
        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(return_value=None)
        logger = MagicMock()
        with pytest.raises(HTTPException) as exc:
            await _get_user_key("u1", gp, logger)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_user_not_dict_raises_404(self):
        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(return_value="not-a-dict")
        logger = MagicMock()
        with pytest.raises(HTTPException) as exc:
            await _get_user_key("u1", gp, logger)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_exception_raises_500(self):
        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(side_effect=RuntimeError("db down"))
        logger = MagicMock()
        with pytest.raises(HTTPException) as exc:
            await _get_user_key("u1", gp, logger)
        assert exc.value.status_code == 500


# ============================================================================
# _edge_source_key
# ============================================================================

class TestEdgeSourceKey:
    def test_from_id_present(self):
        assert _edge_source_key({"from_id": "agent123"}) == "agent123"

    def test_arango_from_field(self):
        assert _edge_source_key({"_from": "agentInstances/agent123"}) == "agent123"

    def test_neither_field_returns_empty(self):
        assert _edge_source_key({}) == ""

    def test_from_id_takes_precedence(self):
        assert _edge_source_key({"from_id": "neo4j-key", "_from": "col/arango-key"}) == "neo4j-key"

    def test_arango_from_no_slash(self):
        assert _edge_source_key({"_from": "barekey"}) == "barekey"


# ============================================================================
# _handle_registry_error / _handle_format_error
# ============================================================================

class TestHandleErrors:
    def test_registry_not_found(self):
        exc = _handle_registry_error(RegistryError("skill 'foo' not found"))
        assert exc.status_code == 404

    def test_registry_conflict(self):
        exc = _handle_registry_error(RegistryError("skill already exists"))
        assert exc.status_code == 409

    def test_format_error(self):
        exc = _handle_format_error(SkillFormatError("bad format"))
        assert exc.status_code == 400


# ============================================================================
# list_skills
# ============================================================================

class TestListSkills:
    @pytest.mark.asyncio
    async def test_success(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        metadata = _mock_metadata()
        manager.list_skills = AsyncMock(return_value=[metadata])

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import list_skills
            resp = await list_skills(req)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_with_filters(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.list_skills = AsyncMock(return_value=[])

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import list_skills
            resp = await list_skills(req, category="general", tag="test", q="search")
        assert resp.status_code == 200


# ============================================================================
# get_categories
# ============================================================================

class TestGetCategories:
    @pytest.mark.asyncio
    async def test_success(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.get_categories = AsyncMock(return_value=["general"])
        manager.get_tags = AsyncMock(return_value=["test"])

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import get_categories
            resp = await get_categories(req)
        assert resp.status_code == 200
        body = resp.body.decode()
        assert "general" in body
        assert "test" in body


# ============================================================================
# search_skills
# ============================================================================

class TestSearchSkills:
    @pytest.mark.asyncio
    async def test_success(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        match = MagicMock()
        match.skill = _mock_metadata()
        match.relevance = 0.9
        match.match_reason = "name"
        manager.search = AsyncMock(return_value=[match])

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import search_skills
            resp = await search_skills(req, q="test")
        assert resp.status_code == 200


# ============================================================================
# get_skill
# ============================================================================

class TestGetSkill:
    @pytest.mark.asyncio
    async def test_success(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.activate_skill = AsyncMock(return_value=_mock_skill())

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import get_skill
            resp = await get_skill(req, "test-skill")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_not_found(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.activate_skill = AsyncMock(side_effect=RegistryError("skill 'x' not found"))

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import get_skill
            with pytest.raises(HTTPException) as exc:
                await get_skill(req, "x")
            assert exc.value.status_code == 404


# ============================================================================
# export_skill
# ============================================================================

class TestExportSkill:
    @pytest.mark.asyncio
    async def test_success(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.activate_skill = AsyncMock(return_value=_mock_skill())

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)), \
             patch(f"{MODULE}.render_skill_md", return_value="---\nname: test\n---\nbody"):
            from app.api.routes.skills import export_skill
            resp = await export_skill(req, "test-skill")
        assert resp.status_code == 200
        assert resp.media_type == "text/markdown"

    @pytest.mark.asyncio
    async def test_not_found(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.activate_skill = AsyncMock(side_effect=RegistryError("not found"))

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import export_skill
            with pytest.raises(HTTPException) as exc:
                await export_skill(req, "x")
            assert exc.value.status_code == 404


# ============================================================================
# create_skill
# ============================================================================

class TestCreateSkill:
    @pytest.mark.asyncio
    async def test_success(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.create = AsyncMock(return_value=_mock_metadata())
        payload = SkillWriteRequest(name="new-skill", description="desc", body="body")

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)), \
             patch(f"{MODULE}._build_content", return_value="---\nname: new-skill\n---\nbody"):
            from app.api.routes.skills import create_skill
            resp = await create_skill(req, payload)
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_missing_name_raises_400(self):
        req = _mock_request()
        payload = SkillWriteRequest(name="", description="desc", body="body")

        from app.api.routes.skills import create_skill
        with pytest.raises(HTTPException) as exc:
            await create_skill(req, payload)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_whitespace_name_raises_400(self):
        req = _mock_request()
        payload = SkillWriteRequest(name="   ", description="desc", body="body")

        from app.api.routes.skills import create_skill
        with pytest.raises(HTTPException) as exc:
            await create_skill(req, payload)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_registry_error(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.create = AsyncMock(side_effect=RegistryError("already exists"))
        payload = SkillWriteRequest(name="dup", description="desc", body="body")

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)), \
             patch(f"{MODULE}._build_content", return_value="content"):
            from app.api.routes.skills import create_skill
            with pytest.raises(HTTPException) as exc:
                await create_skill(req, payload)
            assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_format_error(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.create = AsyncMock(side_effect=SkillFormatError("bad yaml"))
        payload = SkillWriteRequest(name="bad", description="desc", body="body")

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)), \
             patch(f"{MODULE}._build_content", return_value="content"):
            from app.api.routes.skills import create_skill
            with pytest.raises(HTTPException) as exc:
                await create_skill(req, payload)
            assert exc.value.status_code == 400


# ============================================================================
# update_skill
# ============================================================================

class TestUpdateSkill:
    @pytest.mark.asyncio
    async def test_success(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.update = AsyncMock(return_value=_mock_metadata())
        payload = SkillWriteRequest(description="updated", body="new body")

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)), \
             patch(f"{MODULE}._build_content", return_value="content"):
            from app.api.routes.skills import update_skill
            resp = await update_skill(req, "test-skill", payload)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_registry_error(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.update = AsyncMock(side_effect=RegistryError("not found"))
        payload = SkillWriteRequest(description="x", body="y")

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)), \
             patch(f"{MODULE}._build_content", return_value="content"):
            from app.api.routes.skills import update_skill
            with pytest.raises(HTTPException) as exc:
                await update_skill(req, "x", payload)
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_format_error(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.update = AsyncMock(side_effect=SkillFormatError("invalid"))
        payload = SkillWriteRequest(description="x", body="y")

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)), \
             patch(f"{MODULE}._build_content", return_value="content"):
            from app.api.routes.skills import update_skill
            with pytest.raises(HTTPException) as exc:
                await update_skill(req, "x", payload)
            assert exc.value.status_code == 400


# ============================================================================
# patch_skill_body
# ============================================================================

class TestPatchSkillBody:
    @pytest.mark.asyncio
    async def test_success(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.patch = AsyncMock(return_value=True)
        payload = PatchBodyRequest(old_string="old", new_string="new")

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import patch_skill_body
            resp = await patch_skill_body(req, "test-skill", payload)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_patch_fails_raises_400(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.patch = AsyncMock(return_value=False)
        payload = PatchBodyRequest(old_string="missing", new_string="new")

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import patch_skill_body
            with pytest.raises(HTTPException) as exc:
                await patch_skill_body(req, "test-skill", payload)
            assert exc.value.status_code == 400


# ============================================================================
# deprecate_skill
# ============================================================================

class TestDeprecateSkill:
    @pytest.mark.asyncio
    async def test_success(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.deprecate = AsyncMock(return_value=True)
        payload = DeprecateRequest(reason="outdated", replaced_by="new-skill")

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import deprecate_skill
            resp = await deprecate_skill(req, "old-skill", payload)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_not_found(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.deprecate = AsyncMock(return_value=False)
        payload = DeprecateRequest(reason="gone")

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import deprecate_skill
            with pytest.raises(HTTPException) as exc:
                await deprecate_skill(req, "missing", payload)
            assert exc.value.status_code == 404


# ============================================================================
# _check_usage
# ============================================================================

class TestCheckUsage:
    @pytest.mark.asyncio
    async def test_no_usage(self):
        gp = AsyncMock()
        gp.get_edges_to_node = AsyncMock(return_value=[])

        from app.api.routes.skills import _check_usage
        usage = await _check_usage("my-skill", "o1", gp)
        assert usage["usedByAgents"] == []
        assert usage["requiredBySkills"] == []

    @pytest.mark.asyncio
    async def test_agent_usage(self):
        gp = AsyncMock()
        gp.get_edges_to_node = AsyncMock(side_effect=[
            [{"from_id": "agent1"}],
            [],
        ])
        gp.get_document = AsyncMock(return_value={"name": "My Agent"})

        from app.api.routes.skills import _check_usage
        usage = await _check_usage("my-skill", "o1", gp)
        assert len(usage["usedByAgents"]) == 1
        assert usage["usedByAgents"][0]["id"] == "agent1"
        assert usage["usedByAgents"][0]["name"] == "My Agent"

    @pytest.mark.asyncio
    async def test_skill_dependency(self):
        gp = AsyncMock()
        gp.get_edges_to_node = AsyncMock(side_effect=[
            [],
            [{"from_id": "o1_other-skill", "type": "requires"}],
        ])

        from app.api.routes.skills import _check_usage
        usage = await _check_usage("my-skill", "o1", gp)
        assert "other-skill" in usage["requiredBySkills"]

    @pytest.mark.asyncio
    async def test_edge_with_empty_source_key_skipped(self):
        gp = AsyncMock()
        gp.get_edges_to_node = AsyncMock(side_effect=[
            [{}],
            [],
        ])

        from app.api.routes.skills import _check_usage
        usage = await _check_usage("my-skill", "o1", gp)
        assert usage["usedByAgents"] == []

    @pytest.mark.asyncio
    async def test_agent_doc_none_skipped(self):
        gp = AsyncMock()
        gp.get_edges_to_node = AsyncMock(side_effect=[
            [{"from_id": "agent1"}],
            [],
        ])
        gp.get_document = AsyncMock(return_value=None)

        from app.api.routes.skills import _check_usage
        usage = await _check_usage("my-skill", "o1", gp)
        assert usage["usedByAgents"] == []

    @pytest.mark.asyncio
    async def test_relation_non_requires_type_ignored(self):
        gp = AsyncMock()
        gp.get_edges_to_node = AsyncMock(side_effect=[
            [],
            [{"from_id": "o1_other", "type": "related"}],
        ])

        from app.api.routes.skills import _check_usage
        usage = await _check_usage("my-skill", "o1", gp)
        assert usage["requiredBySkills"] == []


# ============================================================================
# get_skill_usage
# ============================================================================

class TestGetSkillUsage:
    @pytest.mark.asyncio
    async def test_success(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)), \
             patch(f"{MODULE}._check_usage", new_callable=AsyncMock, return_value={"usedByAgents": [], "requiredBySkills": []}):
            from app.api.routes.skills import get_skill_usage
            resp = await get_skill_usage(req, "test-skill")
        assert resp.status_code == 200


# ============================================================================
# delete_skill
# ============================================================================

class TestDeleteSkill:
    @pytest.mark.asyncio
    async def test_simple_delete_success(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.delete = AsyncMock(return_value=True)

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)), \
             patch(f"{MODULE}._check_usage", new_callable=AsyncMock,
                   return_value={"usedByAgents": [], "requiredBySkills": []}):
            from app.api.routes.skills import delete_skill
            resp = await delete_skill(req, "test-skill")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_required_by_skills_blocks_409(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)), \
             patch(f"{MODULE}._check_usage", new_callable=AsyncMock,
                   return_value={"usedByAgents": [], "requiredBySkills": ["dep-skill"]}):
            from app.api.routes.skills import delete_skill
            with pytest.raises(HTTPException) as exc:
                await delete_skill(req, "base-skill")
            assert exc.value.status_code == 409
            assert "required by" in str(exc.value.detail["message"])

    @pytest.mark.asyncio
    async def test_used_by_agents_without_detach_blocks_409(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)), \
             patch(f"{MODULE}._check_usage", new_callable=AsyncMock,
                   return_value={"usedByAgents": [{"id": "a1", "name": "Agent1"}], "requiredBySkills": []}):
            from app.api.routes.skills import delete_skill
            with pytest.raises(HTTPException) as exc:
                await delete_skill(req, "used-skill", detach=False)
            assert exc.value.status_code == 409
            assert "assigned to" in str(exc.value.detail["message"])

    @pytest.mark.asyncio
    async def test_used_by_agents_with_detach_succeeds(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.delete = AsyncMock(return_value=True)

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)), \
             patch(f"{MODULE}._check_usage", new_callable=AsyncMock,
                   return_value={"usedByAgents": [{"id": "a1", "name": "Agent1"}], "requiredBySkills": []}):
            from app.api.routes.skills import delete_skill
            resp = await delete_skill(req, "used-skill", detach=True)
        assert resp.status_code == 200
        ctx["graph_provider"].batch_delete_edges.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_not_found_raises_404(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.delete = AsyncMock(return_value=False)

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)), \
             patch(f"{MODULE}._check_usage", new_callable=AsyncMock,
                   return_value={"usedByAgents": [], "requiredBySkills": []}):
            from app.api.routes.skills import delete_skill
            with pytest.raises(HTTPException) as exc:
                await delete_skill(req, "ghost")
            assert exc.value.status_code == 404


# ============================================================================
# list_versions
# ============================================================================

class TestListVersions:
    @pytest.mark.asyncio
    async def test_success(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.list_versions = AsyncMock(return_value=[_mock_version_entry()])

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import list_versions
            resp = await list_versions(req, "test-skill")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_registry_error(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.list_versions = AsyncMock(side_effect=RegistryError("not found"))

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import list_versions
            with pytest.raises(HTTPException) as exc:
                await list_versions(req, "x")
            assert exc.value.status_code == 404


# ============================================================================
# get_version
# ============================================================================

class TestGetVersion:
    @pytest.mark.asyncio
    async def test_success(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.get_version = AsyncMock(return_value=_mock_skill())

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import get_version
            resp = await get_version(req, "test-skill", "1.0.0")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_version_none_raises_404(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.get_version = AsyncMock(return_value=None)

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import get_version
            with pytest.raises(HTTPException) as exc:
                await get_version(req, "test-skill", "9.9.9")
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_registry_error(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.get_version = AsyncMock(side_effect=RegistryError("not found"))

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import get_version
            with pytest.raises(HTTPException) as exc:
                await get_version(req, "x", "1.0.0")
            assert exc.value.status_code == 404


# ============================================================================
# rollback_skill
# ============================================================================

class TestRollbackSkill:
    @pytest.mark.asyncio
    async def test_success(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.rollback = AsyncMock(return_value=_mock_metadata())
        payload = RollbackRequest(version="1.0.0")

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import rollback_skill
            resp = await rollback_skill(req, "test-skill", payload)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_registry_error(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.rollback = AsyncMock(side_effect=RegistryError("not found"))
        payload = RollbackRequest(version="0.0.1")

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import rollback_skill
            with pytest.raises(HTTPException) as exc:
                await rollback_skill(req, "x", payload)
            assert exc.value.status_code == 404


# ============================================================================
# get_resource / write_resource / remove_resource
# ============================================================================

class TestResourceRoutes:
    @pytest.mark.asyncio
    async def test_get_resource_success(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.load_resource = AsyncMock(return_value="resource content")

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import get_resource
            resp = await get_resource(req, "test-skill", path="data.json")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_resource_registry_error(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.load_resource = AsyncMock(side_effect=RegistryError("not found"))

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import get_resource
            with pytest.raises(HTTPException) as exc:
                await get_resource(req, "x", path="data.json")
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_write_resource_success(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.write_resource = AsyncMock(return_value=True)
        payload = ResourceWriteRequest(path="data.json", content='{"key": "value"}')

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import write_resource
            resp = await write_resource(req, "test-skill", payload)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_write_resource_not_found(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.write_resource = AsyncMock(return_value=False)
        payload = ResourceWriteRequest(path="data.json", content="x")

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import write_resource
            with pytest.raises(HTTPException) as exc:
                await write_resource(req, "ghost", payload)
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_remove_resource_success(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.remove_resource = AsyncMock(return_value=True)

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import remove_resource
            resp = await remove_resource(req, "test-skill", path="data.json")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_remove_resource_not_found(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.remove_resource = AsyncMock(return_value=False)

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import remove_resource
            with pytest.raises(HTTPException) as exc:
                await remove_resource(req, "x", path="missing.json")
            assert exc.value.status_code == 404


# ============================================================================
# Candidates
# ============================================================================

class TestCandidateRoutes:
    @pytest.mark.asyncio
    async def test_get_pending_candidates(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        candidate = MagicMock()
        candidate.model_dump.return_value = {"id": "c1", "status": "pending"}
        manager.get_pending_candidates = AsyncMock(return_value=[candidate])

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import get_pending_candidates
            resp = await get_pending_candidates(req)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_approve_candidate_success(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.approve_candidate = AsyncMock(return_value=_mock_metadata())

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import approve_candidate
            resp = await approve_candidate(req, "c1")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_approve_candidate_registry_error(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.approve_candidate = AsyncMock(side_effect=RegistryError("not found"))

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import approve_candidate
            with pytest.raises(HTTPException) as exc:
                await approve_candidate(req, "c1")
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_reject_candidate(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.reject_candidate = AsyncMock()

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import reject_candidate
            resp = await reject_candidate(req, "c1")
        assert resp.status_code == 200


# ============================================================================
# Import routes
# ============================================================================

class TestImportRoutes:
    @pytest.mark.asyncio
    async def test_preview_npm_success(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        mock_preview = MagicMock()
        mock_preview.name = "pkg"
        mock_preview.description = "desc"
        mock_preview.version = "1.0.0"
        mock_preview.content = "---\nname: pkg\n---\nbody"
        mock_preview.resources = {}
        mock_preview.warnings = []
        mock_preview.skipped_binary_resources = []
        mock_preview.source_label = "npm:pkg"

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)), \
             patch(f"{MODULE}.parse_npm_command", return_value=MagicMock()), \
             patch(f"{MODULE}.SkillPackageImporter") as MockImporter:
            MockImporter.return_value.preview_npm = AsyncMock(return_value=mock_preview)
            payload = NpmImportRequest(command_or_name="@scope/pkg")
            from app.api.routes.skills import preview_npm_import
            resp = await preview_npm_import(req, payload)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_preview_npm_parse_error(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()

        from app.services.skills.npm_command_parser import NpmCommandParseError
        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)), \
             patch(f"{MODULE}.parse_npm_command", side_effect=NpmCommandParseError("bad command")):
            payload = NpmImportRequest(command_or_name="???")
            from app.api.routes.skills import preview_npm_import
            with pytest.raises(HTTPException) as exc:
                await preview_npm_import(req, payload)
            assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_preview_npm_import_error(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()

        from app.services.skills.package_importer import PackageImportError
        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)), \
             patch(f"{MODULE}.parse_npm_command", return_value=MagicMock()), \
             patch(f"{MODULE}.SkillPackageImporter") as MockImporter:
            MockImporter.return_value.preview_npm = AsyncMock(side_effect=PackageImportError("fetch failed"))
            payload = NpmImportRequest(command_or_name="@scope/pkg")
            from app.api.routes.skills import preview_npm_import
            with pytest.raises(HTTPException) as exc:
                await preview_npm_import(req, payload)
            assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_preview_url_success(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        mock_preview = MagicMock()
        mock_preview.name = "url-skill"
        mock_preview.description = "desc"
        mock_preview.version = "1.0.0"
        mock_preview.content = "content"
        mock_preview.resources = {}
        mock_preview.warnings = []
        mock_preview.skipped_binary_resources = []
        mock_preview.source_label = "url"

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)), \
             patch(f"{MODULE}.SkillPackageImporter") as MockImporter:
            MockImporter.return_value.preview_url = AsyncMock(return_value=mock_preview)
            payload = UrlImportRequest(url="https://example.com/skill.md")
            from app.api.routes.skills import preview_url_import
            resp = await preview_url_import(req, payload)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_preview_url_import_error(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()

        from app.services.skills.package_importer import PackageImportError
        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)), \
             patch(f"{MODULE}.SkillPackageImporter") as MockImporter:
            MockImporter.return_value.preview_url = AsyncMock(side_effect=PackageImportError("fail"))
            payload = UrlImportRequest(url="https://bad.com")
            from app.api.routes.skills import preview_url_import
            with pytest.raises(HTTPException) as exc:
                await preview_url_import(req, payload)
            assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_preview_upload_success(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        mock_preview = MagicMock()
        mock_preview.name = "upload-skill"
        mock_preview.description = "desc"
        mock_preview.version = "1.0.0"
        mock_preview.content = "content"
        mock_preview.resources = {}
        mock_preview.warnings = []
        mock_preview.skipped_binary_resources = []
        mock_preview.source_label = "upload"

        mock_file = MagicMock()
        mock_file.filename = "skill.zip"
        mock_file.read = AsyncMock(return_value=b"zipdata")

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)), \
             patch(f"{MODULE}.SkillPackageImporter") as MockImporter:
            MockImporter.return_value.preview_upload = MagicMock(return_value=mock_preview)
            from app.api.routes.skills import preview_upload_import
            resp = await preview_upload_import(req, mock_file)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_preview_upload_import_error(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()

        mock_file = MagicMock()
        mock_file.filename = "bad.zip"
        mock_file.read = AsyncMock(return_value=b"data")

        from app.services.skills.package_importer import PackageImportError
        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)), \
             patch(f"{MODULE}.SkillPackageImporter") as MockImporter:
            MockImporter.return_value.preview_upload = MagicMock(side_effect=PackageImportError("corrupt"))
            from app.api.routes.skills import preview_upload_import
            with pytest.raises(HTTPException) as exc:
                await preview_upload_import(req, mock_file)
            assert exc.value.status_code == 400


# ============================================================================
# finalize_import
# ============================================================================

class TestFinalizeImport:
    @pytest.mark.asyncio
    async def test_success(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.create = AsyncMock(return_value=_mock_metadata())
        manager.write_resource = AsyncMock(return_value=True)
        payload = FinalizeImportRequest(
            content="---\nname: imported-skill\n---\nbody",
            resources={"ref.md": "# ref"},
        )

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import finalize_import
            resp = await finalize_import(req, payload)
        assert resp.status_code == 201
        manager.write_resource.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_frontmatter_raises_400(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        payload = FinalizeImportRequest(content="no frontmatter here")

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import finalize_import
            with pytest.raises(HTTPException) as exc:
                await finalize_import(req, payload)
            assert exc.value.status_code == 400
            assert "missing a 'name'" in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_missing_name_in_frontmatter_raises_400(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        payload = FinalizeImportRequest(content="---\ndescription: no name\n---\nbody")

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import finalize_import
            with pytest.raises(HTTPException) as exc:
                await finalize_import(req, payload)
            assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_yaml_raises_400(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        payload = FinalizeImportRequest(content="---\n: :\n: bad yaml [[\n---\nbody")

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import finalize_import
            with pytest.raises(HTTPException) as exc:
                await finalize_import(req, payload)
            assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_registry_error_on_create(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.create = AsyncMock(side_effect=RegistryError("already exists"))
        payload = FinalizeImportRequest(content="---\nname: dup\n---\nbody")

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import finalize_import
            with pytest.raises(HTTPException) as exc:
                await finalize_import(req, payload)
            assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_format_error_on_create(self):
        req = _mock_request()
        manager, ctx = _mock_manager_and_ctx()
        manager.create = AsyncMock(side_effect=SkillFormatError("invalid format"))
        payload = FinalizeImportRequest(content="---\nname: bad\n---\nbody")

        with patch(f"{MODULE}._build_manager", new_callable=AsyncMock, return_value=(manager, ctx)):
            from app.api.routes.skills import finalize_import
            with pytest.raises(HTTPException) as exc:
                await finalize_import(req, payload)
            assert exc.value.status_code == 400
