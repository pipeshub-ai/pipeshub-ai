"""Tests for app.api.routes.skills — pure helper functions and safe-delete
route logic, following the direct-handler-call style used in test_agent.py
(route functions are plain async functions; `_build_manager` is patched
rather than exercising the real DI container/TestClient)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.base import (
    Skill,
    SkillMetadata,
    SkillSource,
    SkillStatus,
)
from app.agent_loop_lib.modules.providers.skills.validator import SkillFormatError


def _metadata(**overrides: object) -> SkillMetadata:
    defaults = {"name": "pdf-extractor", "description": "Extracts tables from PDFs"}
    defaults.update(overrides)
    return SkillMetadata(**defaults)


class TestMetadataToDict:
    def test_maps_all_fields(self) -> None:
        from app.api.routes.skills import _metadata_to_dict

        meta = _metadata(
            version="2.0.0", category="documents", subcategory="pdf", tags=["pdf", "tables"],
            status=SkillStatus.DEPRECATED, source=SkillSource.IMPORTED, license="MIT",
        )
        out = _metadata_to_dict(meta)
        assert out["name"] == "pdf-extractor"
        assert out["version"] == "2.0.0"
        assert out["category"] == "documents"
        assert out["tags"] == ["pdf", "tables"]
        assert out["status"] == "deprecated"
        assert out["source"] == "imported"
        assert out["license"] == "MIT"

    def test_enum_fields_are_plain_strings_not_enum_members(self) -> None:
        from app.api.routes.skills import _metadata_to_dict

        out = _metadata_to_dict(_metadata())
        assert out["status"] == "active"
        assert isinstance(out["status"], str)
        assert out["source"] == "manual"


class TestSkillToDict:
    def test_includes_body_and_resources(self) -> None:
        from app.api.routes.skills import _skill_to_dict

        skill = Skill(metadata=_metadata(), body="# Instructions", resources={"scripts": ["run.py"]})
        out = _skill_to_dict(skill)
        assert out["body"] == "# Instructions"
        assert out["resources"] == {"scripts": ["run.py"]}
        assert out["name"] == "pdf-extractor"


class TestPreviewToDict:
    def test_maps_import_preview_fields(self) -> None:
        from app.api.routes.skills import _preview_to_dict
        from app.services.skills.package_importer import ImportPreview

        preview = ImportPreview(
            name="pdf-extractor", description="d", version="1.0.0", content="---\nname: x\n---\nbody",
            resources={"scripts/run.py": "print(1)"}, warnings=["w1"],
            skipped_binary_resources=["assets/logo.png"], source_label="npm:pdf-extractor@1.0.0",
        )
        out = _preview_to_dict(preview)
        assert out["sourceLabel"] == "npm:pdf-extractor@1.0.0"
        assert out["skippedBinaryResources"] == ["assets/logo.png"]
        assert out["resources"] == {"scripts/run.py": "print(1)"}


class TestBuildContent:
    def test_renders_valid_frontmatter_and_body(self) -> None:
        from app.api.routes.skills import SkillWriteRequest, _build_content

        payload = SkillWriteRequest(
            name="pdf-extractor", description="Extracts tables from PDFs", body="# How\n\nDo the thing.",
            category="documents", tags=["pdf"],
        )
        content = _build_content(payload, name="pdf-extractor")
        assert content.startswith("---\n")
        assert "name: pdf-extractor" in content
        assert "description: Extracts tables from PDFs" in content
        assert "Do the thing." in content

    def test_uses_name_argument_not_payload_name_for_update(self) -> None:
        """Update requests ignore payload.name — the path param is authoritative."""
        from app.api.routes.skills import SkillWriteRequest, _build_content

        payload = SkillWriteRequest(name="ignored-name", description="d", body="body")
        content = _build_content(payload, name="actual-name")
        assert "name: actual-name" in content
        assert "ignored-name" not in content


class TestErrorHandlers:
    def test_registry_error_not_found_maps_to_404(self) -> None:
        from app.api.routes.skills import _handle_registry_error

        exc = _handle_registry_error(RegistryError("Skill 'x' not found"))
        assert isinstance(exc, HTTPException)
        assert exc.status_code == 404

    def test_registry_error_other_maps_to_409(self) -> None:
        from app.api.routes.skills import _handle_registry_error

        exc = _handle_registry_error(RegistryError("Skill 'x' already exists"))
        assert exc.status_code == 409

    def test_format_error_maps_to_400(self) -> None:
        from app.api.routes.skills import _handle_format_error

        exc = _handle_format_error(SkillFormatError("bad frontmatter"))
        assert exc.status_code == 400
        assert exc.detail == "bad frontmatter"


class TestEdgeSourceKey:
    """`_edge_source_key` must read both graph-provider edge shapes — Arango's
    full `_from` id and Neo4j's already-bare `from_id` (see
    `Neo4jProvider.get_edges_to_node`) — so `_check_usage` works unmodified
    against either backend."""

    def test_reads_arango_from_field(self) -> None:
        from app.api.routes.skills import _edge_source_key

        assert _edge_source_key({"_from": "agentInstances/agent-1"}) == "agent-1"

    def test_reads_neo4j_from_id_field(self) -> None:
        from app.api.routes.skills import _edge_source_key

        assert _edge_source_key({"from_id": "agent-1", "from_collection": "agentInstances"}) == "agent-1"

    def test_prefers_from_id_when_both_present(self) -> None:
        from app.api.routes.skills import _edge_source_key

        assert _edge_source_key({"from_id": "agent-1", "_from": "agentInstances/agent-2"}) == "agent-1"

    def test_missing_both_fields_returns_empty_string(self) -> None:
        from app.api.routes.skills import _edge_source_key

        assert _edge_source_key({}) == ""


class TestCheckUsage:
    @pytest.mark.asyncio
    async def test_no_usage_returns_empty_lists(self) -> None:
        from app.api.routes.skills import _check_usage

        gp = AsyncMock()
        gp.get_edges_to_node = AsyncMock(return_value=[])
        result = await _check_usage("pdf-extractor", "org1", gp)
        assert result == {"usedByAgents": [], "requiredBySkills": []}

    @pytest.mark.asyncio
    async def test_reports_agents_using_the_skill(self) -> None:
        from app.api.routes.skills import _check_usage

        gp = AsyncMock()

        async def fake_edges(node_id: str, collection: str) -> list[dict]:
            if collection == "agentHasSkill":
                return [{"_from": "agentInstances/agent-1"}]
            return []

        gp.get_edges_to_node = AsyncMock(side_effect=fake_edges)
        gp.get_document = AsyncMock(return_value={"name": "My Support Bot"})
        result = await _check_usage("pdf-extractor", "org1", gp)
        assert result["usedByAgents"] == [{"id": "agent-1", "name": "My Support Bot"}]
        assert result["requiredBySkills"] == []

    @pytest.mark.asyncio
    async def test_reports_agents_using_the_skill_on_neo4j_edge_shape(self) -> None:
        """Same as above but with Neo4j's generic `from_id`/`from_collection`
        edge shape instead of Arango's `_from` — see `Neo4jProvider.get_edges_to_node`."""
        from app.api.routes.skills import _check_usage

        gp = AsyncMock()

        async def fake_edges(node_id: str, collection: str) -> list[dict]:
            if collection == "agentHasSkill":
                return [{"from_id": "agent-1", "from_collection": "agentInstances", "to_id": "org1_pdf-extractor"}]
            return []

        gp.get_edges_to_node = AsyncMock(side_effect=fake_edges)
        gp.get_document = AsyncMock(return_value={"name": "My Support Bot"})
        result = await _check_usage("pdf-extractor", "org1", gp)
        assert result["usedByAgents"] == [{"id": "agent-1", "name": "My Support Bot"}]
        assert result["requiredBySkills"] == []

    @pytest.mark.asyncio
    async def test_skips_agent_edge_whose_agent_doc_is_gone(self) -> None:
        from app.api.routes.skills import _check_usage

        gp = AsyncMock()

        async def fake_edges(node_id: str, collection: str) -> list[dict]:
            if collection == "agentHasSkill":
                return [{"_from": "agentInstances/deleted-agent"}]
            return []

        gp.get_edges_to_node = AsyncMock(side_effect=fake_edges)
        gp.get_document = AsyncMock(return_value=None)
        result = await _check_usage("pdf-extractor", "org1", gp)
        assert result["usedByAgents"] == []

    @pytest.mark.asyncio
    async def test_reports_skills_that_require_this_one(self) -> None:
        from app.api.routes.skills import _check_usage

        gp = AsyncMock()

        async def fake_edges(node_id: str, collection: str) -> list[dict]:
            if collection == "agentSkillRelation":
                return [
                    {"_from": "agentSkills/org1_dependent-skill", "type": "requires"},
                    {"_from": "agentSkills/org1_unrelated-skill", "type": "related"},
                ]
            return []

        gp.get_edges_to_node = AsyncMock(side_effect=fake_edges)
        result = await _check_usage("pdf-extractor", "org1", gp)
        assert result["requiredBySkills"] == ["dependent-skill"]

    @pytest.mark.asyncio
    async def test_reports_skills_that_require_this_one_on_neo4j_edge_shape(self) -> None:
        from app.api.routes.skills import _check_usage

        gp = AsyncMock()

        async def fake_edges(node_id: str, collection: str) -> list[dict]:
            if collection == "agentSkillRelation":
                return [
                    {"from_id": "org1_dependent-skill", "from_collection": "agentSkills", "type": "requires"},
                    {"from_id": "org1_unrelated-skill", "from_collection": "agentSkills", "type": "related"},
                ]
            return []

        gp.get_edges_to_node = AsyncMock(side_effect=fake_edges)
        result = await _check_usage("pdf-extractor", "org1", gp)
        assert result["requiredBySkills"] == ["dependent-skill"]


class TestDeleteSkillRoute:
    def _ctx(self, graph_provider: AsyncMock) -> dict:
        return {"graph_provider": graph_provider, "orgId": "org1", "logger": MagicMock()}

    @pytest.mark.asyncio
    async def test_blocks_delete_when_required_by_other_skills(self) -> None:
        from app.api.routes.skills import delete_skill

        manager = AsyncMock()
        gp = AsyncMock()
        with (
            patch("app.api.routes.skills._build_manager", new=AsyncMock(return_value=(manager, self._ctx(gp)))),
            patch(
                "app.api.routes.skills._check_usage",
                new=AsyncMock(return_value={"usedByAgents": [], "requiredBySkills": ["other-skill"]}),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await delete_skill(MagicMock(), "pdf-extractor", detach=False)
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["requiredBySkills"] == ["other-skill"]
        manager.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_blocks_delete_when_used_by_agents_without_detach(self) -> None:
        from app.api.routes.skills import delete_skill

        manager = AsyncMock()
        gp = AsyncMock()
        usage = {"usedByAgents": [{"id": "agent-1", "name": "Support Bot"}], "requiredBySkills": []}
        with (
            patch("app.api.routes.skills._build_manager", new=AsyncMock(return_value=(manager, self._ctx(gp)))),
            patch("app.api.routes.skills._check_usage", new=AsyncMock(return_value=usage)),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await delete_skill(MagicMock(), "pdf-extractor", detach=False)
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["usedByAgents"] == usage["usedByAgents"]
        manager.delete.assert_not_called()
        gp.batch_delete_edges.assert_not_called()

    @pytest.mark.asyncio
    async def test_detach_true_removes_edges_then_deletes(self) -> None:
        from app.api.routes.skills import delete_skill

        manager = AsyncMock()
        manager.delete = AsyncMock(return_value=True)
        gp = AsyncMock()
        usage = {"usedByAgents": [{"id": "agent-1", "name": "Support Bot"}], "requiredBySkills": []}
        with (
            patch("app.api.routes.skills._build_manager", new=AsyncMock(return_value=(manager, self._ctx(gp)))),
            patch("app.api.routes.skills._check_usage", new=AsyncMock(return_value=usage)),
        ):
            response = await delete_skill(MagicMock(), "pdf-extractor", detach=True)
        gp.batch_delete_edges.assert_awaited_once()
        manager.delete.assert_awaited_once_with("pdf-extractor")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_no_usage_deletes_cleanly(self) -> None:
        from app.api.routes.skills import delete_skill

        manager = AsyncMock()
        manager.delete = AsyncMock(return_value=True)
        gp = AsyncMock()
        usage = {"usedByAgents": [], "requiredBySkills": []}
        with (
            patch("app.api.routes.skills._build_manager", new=AsyncMock(return_value=(manager, self._ctx(gp)))),
            patch("app.api.routes.skills._check_usage", new=AsyncMock(return_value=usage)),
        ):
            response = await delete_skill(MagicMock(), "pdf-extractor", detach=False)
        assert response.status_code == 200
        gp.batch_delete_edges.assert_not_called()

    @pytest.mark.asyncio
    async def test_manager_delete_returning_false_raises_404(self) -> None:
        from app.api.routes.skills import delete_skill

        manager = AsyncMock()
        manager.delete = AsyncMock(return_value=False)
        gp = AsyncMock()
        usage = {"usedByAgents": [], "requiredBySkills": []}
        with (
            patch("app.api.routes.skills._build_manager", new=AsyncMock(return_value=(manager, self._ctx(gp)))),
            patch("app.api.routes.skills._check_usage", new=AsyncMock(return_value=usage)),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await delete_skill(MagicMock(), "missing-skill", detach=False)
        assert exc_info.value.status_code == 404


class TestCreateSkillRoute:
    @pytest.mark.asyncio
    async def test_missing_name_raises_400(self) -> None:
        from app.api.routes.skills import SkillWriteRequest, create_skill

        payload = SkillWriteRequest(description="d", body="b")
        with pytest.raises(HTTPException) as exc_info:
            await create_skill(MagicMock(), payload)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_registry_conflict_maps_to_409(self) -> None:
        from app.api.routes.skills import SkillWriteRequest, create_skill

        manager = AsyncMock()
        manager.create = AsyncMock(side_effect=RegistryError("Skill 'pdf-extractor' already exists"))
        payload = SkillWriteRequest(name="pdf-extractor", description="d", body="b")
        with patch("app.api.routes.skills._build_manager", new=AsyncMock(return_value=(manager, {}))):
            with pytest.raises(HTTPException) as exc_info:
                await create_skill(MagicMock(), payload)
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_successful_create_returns_201(self) -> None:
        from app.api.routes.skills import SkillWriteRequest, create_skill

        manager = AsyncMock()
        manager.create = AsyncMock(return_value=_metadata())
        payload = SkillWriteRequest(name="pdf-extractor", description="d", body="b")
        with patch("app.api.routes.skills._build_manager", new=AsyncMock(return_value=(manager, {}))):
            response = await create_skill(MagicMock(), payload)
        assert response.status_code == 201


class TestFinalizeImportRoute:
    @pytest.mark.asyncio
    async def test_missing_name_in_content_raises_400(self) -> None:
        from app.api.routes.skills import FinalizeImportRequest, finalize_import

        payload = FinalizeImportRequest(content="not frontmatter at all")
        manager = AsyncMock()
        with patch("app.api.routes.skills._build_manager", new=AsyncMock(return_value=(manager, {}))):
            with pytest.raises(HTTPException) as exc_info:
                await finalize_import(MagicMock(), payload)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_valid_content_persists_skill_and_resources(self) -> None:
        from app.api.routes.skills import FinalizeImportRequest, finalize_import

        manager = AsyncMock()
        manager.create = AsyncMock(return_value=_metadata())
        manager.write_resource = AsyncMock(return_value=True)
        content = "---\nname: pdf-extractor\ndescription: d\n---\nbody"
        payload = FinalizeImportRequest(content=content, resources={"scripts/run.py": "print(1)"})
        with patch("app.api.routes.skills._build_manager", new=AsyncMock(return_value=(manager, {}))):
            response = await finalize_import(MagicMock(), payload)
        manager.create.assert_awaited_once()
        manager.write_resource.assert_awaited_once_with("pdf-extractor", "scripts/run.py", "print(1)")
        assert response.status_code == 201
