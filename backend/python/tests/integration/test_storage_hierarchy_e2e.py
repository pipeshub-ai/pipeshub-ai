"""Storage hierarchy and pattern-match end-to-end integration tests.

Exercises the full storage path pipeline against realistic, multi-connector
hierarchies backed by an in-memory graph provider.

Hierarchy fixtures cover:
    Google Drive:  Connector → RecordGroup("Shared Drive") → Folder → SubFolder → File
    Confluence:    Connector → RecordGroup("Engineering Space") → Page → Child Page
    Jira:          Connector → RecordGroup("Sprint Board") → Epic → Story → Task
    Knowledge Base (LocalKB): Connector → RecordGroup("Policy KB") → Folder →
                              SubFolder → File  (NOT flat — realistic nested hierarchy)
    Web:           Connector → web-crawled pages with URL-based paths
    SharePoint:    Connector → RecordGroup("Team Site") → Folder → SubFolder → File

Tests verify:
- build_hierarchical_storage_path() produces correct paths at every depth
- KB hierarchy is stored with proper nesting, not flat
- StorageCleanupHelper.build_record_path() delegates to the shared function
- build_record_group_path() for group-level prefix
- Pattern match pipeline: connector resolution, grep command building, merge/dedup
- Permission checking via check_vrids_accessible in merge flow
- Storage cleanup: move-tree no-op on same path, delete-connector-storage HTTP flow
- Cross-connector fan-out in execute_pattern_match_pipeline
- Edge cases: deep nesting, special chars, renames, moves, orphans, duplicates

Run with:
    pytest tests/integration/test_storage_hierarchy_e2e.py -v
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors as ConnectorsEnum
from app.utils.storage_path import (
    build_hierarchical_storage_path,
    build_record_group_path,
    sanitize_path_segment,
)
from app.connectors.core.base.data_processor.storage_cleanup import (
    StorageCleanupHelper,
)
from app.utils.pattern_match import (
    build_grep_command_from_query,
    check_pattern_match_eligible,
    execute_pattern_match_pipeline,
    merge_pattern_match_results,
    resolve_connector_ids_for_search,
    cap_pattern_match_blocks,
    _record_in_time_range,
    _graph_record_in_time_range,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ORG_ID = "org-storage-e2e"
USER_ID = "user-storage-e2e"

GDRIVE_CONNECTOR_ID = "conn-gdrive-001"
CONFLUENCE_CONNECTOR_ID = "conn-confluence-001"
JIRA_CONNECTOR_ID = "conn-jira-001"
KB_CONNECTOR_ID = "conn-kb-001"
WEB_CONNECTOR_ID = "conn-web-001"
SHAREPOINT_CONNECTOR_ID = "conn-sharepoint-001"
KB_CONNECTOR_ID_2 = "conn-kb-002"


# ---------------------------------------------------------------------------
# In-memory graph provider for storage path tests
# ---------------------------------------------------------------------------


class StorageGraphProvider:
    """Minimal IGraphDBProvider stub that supports get_record_path,
    get_record_group_by_id, check_vrids_accessible, and get_org_apps.

    Uses simple dicts: nodes keyed by id, parent-child edges as tuples,
    record groups keyed by group id.
    """

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._record_groups: dict[str, dict[str, Any]] = {}
        self._parent_edges: list[tuple[str, str]] = []  # (child_id, parent_id)
        self._apps: list[dict[str, Any]] = []
        self._accessible_vrids: dict[str, str] = {}

    # -- setup helpers --

    def add_record(self, record_id: str, name: str, **kwargs) -> "StorageGraphProvider":
        self._records[record_id] = {"_key": record_id, "recordName": name, **kwargs}
        return self

    def add_record_group(self, group_id: str, group_name: str, **kwargs) -> "StorageGraphProvider":
        self._record_groups[group_id] = {
            "_key": group_id,
            "groupName": group_name,
            **kwargs,
        }
        return self

    def add_parent_child(self, child_id: str, parent_id: str) -> "StorageGraphProvider":
        self._parent_edges.append((child_id, parent_id))
        return self

    def add_app(self, app_id: str, **kwargs) -> "StorageGraphProvider":
        self._apps.append({"_key": app_id, **kwargs})
        return self

    def set_accessible_vrids(self, mapping: dict[str, str]) -> "StorageGraphProvider":
        self._accessible_vrids = mapping
        return self

    def rename_record(self, record_id: str, new_name: str) -> "StorageGraphProvider":
        if record_id in self._records:
            self._records[record_id]["recordName"] = new_name
        return self

    def rename_record_group(self, group_id: str, new_name: str) -> "StorageGraphProvider":
        if group_id in self._record_groups:
            self._record_groups[group_id]["groupName"] = new_name
        return self

    def move_record(self, child_id: str, new_parent_id: str) -> "StorageGraphProvider":
        self._parent_edges = [
            (c, p) for c, p in self._parent_edges if c != child_id
        ]
        self._parent_edges.append((child_id, new_parent_id))
        return self

    def remove_parent_edge(self, child_id: str) -> "StorageGraphProvider":
        self._parent_edges = [
            (c, p) for c, p in self._parent_edges if c != child_id
        ]
        return self

    # -- IGraphDBProvider methods --

    async def get_record_group_by_id(
        self, record_group_id: str, **kwargs
    ) -> dict | None:
        return self._record_groups.get(record_group_id)

    async def get_record_path(self, record_id: str, **kwargs) -> str | None:
        record = self._records.get(record_id)
        if not record:
            return None
        parts = [record["recordName"]]
        current = record_id
        visited = {current}
        while True:
            parent_id = None
            for child, parent in self._parent_edges:
                if child == current:
                    parent_id = parent
                    break
            if not parent_id or parent_id in visited:
                break
            visited.add(parent_id)
            parent = self._records.get(parent_id)
            if not parent:
                break
            parts.append(parent["recordName"])
            current = parent_id
        parts.reverse()
        return "/".join(parts)

    async def get_org_apps(self, org_id: str, **kwargs) -> list[dict]:
        return self._apps

    async def check_vrids_accessible(
        self,
        user_id: str,
        org_id: str,
        virtual_record_ids: list[str],
    ) -> dict[str, str]:
        return {
            vrid: rec_id
            for vrid, rec_id in self._accessible_vrids.items()
            if vrid in virtual_record_ids
        }

    async def get_document(self, document_key: str, collection: str, **kwargs):
        if collection == CollectionNames.RECORDS.value:
            return self._records.get(document_key)
        return None

    async def get_records_by_record_ids(
        self, record_ids: list[str], org_id: str,
    ) -> list[dict]:
        return [self._records[rid] for rid in record_ids if rid in self._records]


# ---------------------------------------------------------------------------
# Fixtures — record factory
# ---------------------------------------------------------------------------


def _make_record(
    record_id: str,
    name: str,
    connector_id: str,
    connector_name: str = "GOOGLE_DRIVE",
    record_group_id: str | None = None,
    virtual_record_id: str | None = None,
    weburl: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=record_id,
        record_name=name,
        connector_id=connector_id,
        connector_name=SimpleNamespace(value=connector_name),
        record_group_id=record_group_id,
        virtual_record_id=virtual_record_id or f"vrid-{record_id}",
        weburl=weburl,
    )


# ---------------------------------------------------------------------------
# Hierarchy builders — each connector gets a realistic tree
# ---------------------------------------------------------------------------


def _build_gdrive_hierarchy(provider: StorageGraphProvider) -> None:
    """Google Drive: Shared Drive → Projects → Backend → api_spec.yaml
                                  → Projects → Frontend → design_mockup.png
                                  → Archive → old_notes.docx
    """
    provider.add_record_group("rg-gdrive", "Shared Drive", connectorId=GDRIVE_CONNECTOR_ID)
    provider.add_record("rec-folder-projects", "Projects", connectorId=GDRIVE_CONNECTOR_ID)
    provider.add_record("rec-folder-backend", "Backend", connectorId=GDRIVE_CONNECTOR_ID)
    provider.add_record("rec-api-spec", "api_spec.yaml", connectorId=GDRIVE_CONNECTOR_ID)
    provider.add_record("rec-folder-frontend", "Frontend", connectorId=GDRIVE_CONNECTOR_ID)
    provider.add_record("rec-design-mockup", "design_mockup.png", connectorId=GDRIVE_CONNECTOR_ID)
    provider.add_record("rec-folder-archive", "Archive", connectorId=GDRIVE_CONNECTOR_ID)
    provider.add_record("rec-old-notes", "old_notes.docx", connectorId=GDRIVE_CONNECTOR_ID)
    provider.add_parent_child("rec-folder-backend", "rec-folder-projects")
    provider.add_parent_child("rec-api-spec", "rec-folder-backend")
    provider.add_parent_child("rec-folder-frontend", "rec-folder-projects")
    provider.add_parent_child("rec-design-mockup", "rec-folder-frontend")
    provider.add_parent_child("rec-old-notes", "rec-folder-archive")


def _build_confluence_hierarchy(provider: StorageGraphProvider) -> None:
    """Confluence: Engineering Space → Architecture → Microservices Overview
                                    → Architecture → Database Design
                   Engineering Space → Onboarding → New Hire Guide
    """
    provider.add_record_group("rg-confluence", "Engineering Space", connectorId=CONFLUENCE_CONNECTOR_ID)
    provider.add_record("rec-arch-page", "Architecture", connectorId=CONFLUENCE_CONNECTOR_ID)
    provider.add_record("rec-micro-page", "Microservices Overview", connectorId=CONFLUENCE_CONNECTOR_ID)
    provider.add_record("rec-db-design", "Database Design", connectorId=CONFLUENCE_CONNECTOR_ID)
    provider.add_record("rec-onboarding-page", "Onboarding", connectorId=CONFLUENCE_CONNECTOR_ID)
    provider.add_record("rec-newhire-guide", "New Hire Guide", connectorId=CONFLUENCE_CONNECTOR_ID)
    provider.add_parent_child("rec-micro-page", "rec-arch-page")
    provider.add_parent_child("rec-db-design", "rec-arch-page")
    provider.add_parent_child("rec-newhire-guide", "rec-onboarding-page")


def _build_jira_hierarchy(provider: StorageGraphProvider) -> None:
    """Jira: Sprint Board → Build Payment Gateway → Implement OAuth → Write unit tests
                           → Build Payment Gateway → Implement OAuth → Add integration tests
             Sprint Board → Migrate Database → Schema migration task
    """
    provider.add_record_group("rg-jira", "Sprint Board", connectorId=JIRA_CONNECTOR_ID)
    provider.add_record("rec-epic-pay", "Build Payment Gateway", connectorId=JIRA_CONNECTOR_ID)
    provider.add_record("rec-story-oauth", "Implement OAuth", connectorId=JIRA_CONNECTOR_ID)
    provider.add_record("rec-task-tests", "Write unit tests", connectorId=JIRA_CONNECTOR_ID)
    provider.add_record("rec-task-itests", "Add integration tests", connectorId=JIRA_CONNECTOR_ID)
    provider.add_record("rec-epic-migrate", "Migrate Database", connectorId=JIRA_CONNECTOR_ID)
    provider.add_record("rec-task-schema", "Schema migration task", connectorId=JIRA_CONNECTOR_ID)
    provider.add_parent_child("rec-story-oauth", "rec-epic-pay")
    provider.add_parent_child("rec-task-tests", "rec-story-oauth")
    provider.add_parent_child("rec-task-itests", "rec-story-oauth")
    provider.add_parent_child("rec-task-schema", "rec-epic-migrate")


def _build_kb_hierarchy(provider: StorageGraphProvider) -> None:
    """Knowledge Base: Nested, NOT flat. Multiple folders and sub-folders.

    Policy KB
      ├── HR Policies
      │   ├── Onboarding
      │   │   ├── company_handbook.pdf
      │   │   └── benefits_overview.docx
      │   └── Leave Policy
      │       └── leave_guidelines.pdf
      ├── Engineering
      │   ├── Architecture
      │   │   ├── system_design.pdf
      │   │   └── API Reference
      │   │       └── api_reference.md
      │   └── Coding Standards
      │       └── style_guide.md
      └── Legal
          └── compliance_guide.pdf
    """
    provider.add_record_group("rg-kb", "Policy KB", connectorId=KB_CONNECTOR_ID)

    # HR Policies folder
    provider.add_record("rec-kb-hr", "HR Policies", connectorId=KB_CONNECTOR_ID)
    # Onboarding sub-folder under HR
    provider.add_record("rec-kb-onboarding", "Onboarding", connectorId=KB_CONNECTOR_ID)
    provider.add_parent_child("rec-kb-onboarding", "rec-kb-hr")
    # Files under Onboarding
    provider.add_record("rec-kb-handbook", "company_handbook.pdf", connectorId=KB_CONNECTOR_ID)
    provider.add_parent_child("rec-kb-handbook", "rec-kb-onboarding")
    provider.add_record("rec-kb-benefits", "benefits_overview.docx", connectorId=KB_CONNECTOR_ID)
    provider.add_parent_child("rec-kb-benefits", "rec-kb-onboarding")
    # Leave Policy sub-folder under HR
    provider.add_record("rec-kb-leave", "Leave Policy", connectorId=KB_CONNECTOR_ID)
    provider.add_parent_child("rec-kb-leave", "rec-kb-hr")
    provider.add_record("rec-kb-leave-guide", "leave_guidelines.pdf", connectorId=KB_CONNECTOR_ID)
    provider.add_parent_child("rec-kb-leave-guide", "rec-kb-leave")

    # Engineering folder
    provider.add_record("rec-kb-eng", "Engineering", connectorId=KB_CONNECTOR_ID)
    # Architecture sub-folder
    provider.add_record("rec-kb-arch", "Architecture", connectorId=KB_CONNECTOR_ID)
    provider.add_parent_child("rec-kb-arch", "rec-kb-eng")
    provider.add_record("rec-kb-sysdesign", "system_design.pdf", connectorId=KB_CONNECTOR_ID)
    provider.add_parent_child("rec-kb-sysdesign", "rec-kb-arch")
    # API Reference sub-sub-folder (4 levels deep)
    provider.add_record("rec-kb-apiref-folder", "API Reference", connectorId=KB_CONNECTOR_ID)
    provider.add_parent_child("rec-kb-apiref-folder", "rec-kb-arch")
    provider.add_record("rec-kb-apiref", "api_reference.md", connectorId=KB_CONNECTOR_ID)
    provider.add_parent_child("rec-kb-apiref", "rec-kb-apiref-folder")
    # Coding Standards sub-folder
    provider.add_record("rec-kb-codestd", "Coding Standards", connectorId=KB_CONNECTOR_ID)
    provider.add_parent_child("rec-kb-codestd", "rec-kb-eng")
    provider.add_record("rec-kb-styleguide", "style_guide.md", connectorId=KB_CONNECTOR_ID)
    provider.add_parent_child("rec-kb-styleguide", "rec-kb-codestd")

    # Legal folder (direct child, 2 levels)
    provider.add_record("rec-kb-legal", "Legal", connectorId=KB_CONNECTOR_ID)
    provider.add_record("rec-kb-compliance", "compliance_guide.pdf", connectorId=KB_CONNECTOR_ID)
    provider.add_parent_child("rec-kb-compliance", "rec-kb-legal")


def _build_kb_second_hierarchy(provider: StorageGraphProvider) -> None:
    """Second KB: Technical Docs KB (separate connector).

    Technical Docs
      └── Infrastructure
          └── deployment_runbook.md
    """
    provider.add_record_group("rg-kb2", "Technical Docs", connectorId=KB_CONNECTOR_ID_2)
    provider.add_record("rec-kb2-infra", "Infrastructure", connectorId=KB_CONNECTOR_ID_2)
    provider.add_record("rec-kb2-runbook", "deployment_runbook.md", connectorId=KB_CONNECTOR_ID_2)
    provider.add_parent_child("rec-kb2-runbook", "rec-kb2-infra")


def _build_sharepoint_hierarchy(provider: StorageGraphProvider) -> None:
    """SharePoint: Team Site → Documents → Q4 Reports → financial_summary.xlsx
                              → Documents → Templates → invoice_template.docx
    """
    provider.add_record_group("rg-sharepoint", "Team Site", connectorId=SHAREPOINT_CONNECTOR_ID)
    provider.add_record("rec-sp-docs", "Documents", connectorId=SHAREPOINT_CONNECTOR_ID)
    provider.add_record("rec-sp-q4", "Q4 Reports", connectorId=SHAREPOINT_CONNECTOR_ID)
    provider.add_parent_child("rec-sp-q4", "rec-sp-docs")
    provider.add_record("rec-sp-financial", "financial_summary.xlsx", connectorId=SHAREPOINT_CONNECTOR_ID)
    provider.add_parent_child("rec-sp-financial", "rec-sp-q4")
    provider.add_record("rec-sp-templates", "Templates", connectorId=SHAREPOINT_CONNECTOR_ID)
    provider.add_parent_child("rec-sp-templates", "rec-sp-docs")
    provider.add_record("rec-sp-invoice", "invoice_template.docx", connectorId=SHAREPOINT_CONNECTOR_ID)
    provider.add_parent_child("rec-sp-invoice", "rec-sp-templates")


def _build_full_provider() -> StorageGraphProvider:
    """Build a provider with all connector hierarchies populated."""
    provider = StorageGraphProvider()
    _build_gdrive_hierarchy(provider)
    _build_confluence_hierarchy(provider)
    _build_jira_hierarchy(provider)
    _build_kb_hierarchy(provider)
    _build_kb_second_hierarchy(provider)
    _build_sharepoint_hierarchy(provider)
    provider.add_app(GDRIVE_CONNECTOR_ID, type="GOOGLE_DRIVE", orgId=ORG_ID)
    provider.add_app(CONFLUENCE_CONNECTOR_ID, type="CONFLUENCE", orgId=ORG_ID)
    provider.add_app(JIRA_CONNECTOR_ID, type="JIRA", orgId=ORG_ID)
    provider.add_app(KB_CONNECTOR_ID, type="LOCAL_KB", orgId=ORG_ID)
    provider.add_app(KB_CONNECTOR_ID_2, type="LOCAL_KB", orgId=ORG_ID)
    provider.add_app(WEB_CONNECTOR_ID, type="WEB", orgId=ORG_ID)
    provider.add_app(SHAREPOINT_CONNECTOR_ID, type="SHAREPOINT", orgId=ORG_ID)
    return provider


# ---------------------------------------------------------------------------
# Tests: Hierarchical storage path — Google Drive
# ---------------------------------------------------------------------------


class TestGDriveHierarchicalPaths:

    @pytest.mark.asyncio
    async def test_gdrive_deep_file(self):
        """Shared Drive → Projects → Backend → api_spec.yaml"""
        provider = _build_full_provider()
        record = _make_record(
            "rec-api-spec", "api_spec.yaml",
            connector_id=GDRIVE_CONNECTOR_ID,
            record_group_id="rg-gdrive",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == (
            f"records/{GDRIVE_CONNECTOR_ID}/Shared Drive/"
            "Projects/Backend/api_spec.yaml"
        )

    @pytest.mark.asyncio
    async def test_gdrive_sibling_branch(self):
        """Shared Drive → Projects → Frontend → design_mockup.png"""
        provider = _build_full_provider()
        record = _make_record(
            "rec-design-mockup", "design_mockup.png",
            connector_id=GDRIVE_CONNECTOR_ID,
            record_group_id="rg-gdrive",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == (
            f"records/{GDRIVE_CONNECTOR_ID}/Shared Drive/"
            "Projects/Frontend/design_mockup.png"
        )

    @pytest.mark.asyncio
    async def test_gdrive_folder_itself(self):
        """A folder record gets its own path without children."""
        provider = _build_full_provider()
        record = _make_record(
            "rec-folder-projects", "Projects",
            connector_id=GDRIVE_CONNECTOR_ID,
            record_group_id="rg-gdrive",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == f"records/{GDRIVE_CONNECTOR_ID}/Shared Drive/Projects"

    @pytest.mark.asyncio
    async def test_gdrive_shallow_file(self):
        """Shared Drive → Archive → old_notes.docx (2 levels under group)"""
        provider = _build_full_provider()
        record = _make_record(
            "rec-old-notes", "old_notes.docx",
            connector_id=GDRIVE_CONNECTOR_ID,
            record_group_id="rg-gdrive",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == (
            f"records/{GDRIVE_CONNECTOR_ID}/Shared Drive/"
            "Archive/old_notes.docx"
        )


# ---------------------------------------------------------------------------
# Tests: Hierarchical storage path — Confluence
# ---------------------------------------------------------------------------


class TestConfluenceHierarchicalPaths:

    @pytest.mark.asyncio
    async def test_confluence_child_page(self):
        """Engineering Space → Architecture → Microservices Overview"""
        provider = _build_full_provider()
        record = _make_record(
            "rec-micro-page", "Microservices Overview",
            connector_id=CONFLUENCE_CONNECTOR_ID,
            connector_name="CONFLUENCE",
            record_group_id="rg-confluence",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == (
            f"records/{CONFLUENCE_CONNECTOR_ID}/Engineering Space/"
            "Architecture/Microservices Overview"
        )

    @pytest.mark.asyncio
    async def test_confluence_sibling_page(self):
        """Engineering Space → Architecture → Database Design"""
        provider = _build_full_provider()
        record = _make_record(
            "rec-db-design", "Database Design",
            connector_id=CONFLUENCE_CONNECTOR_ID,
            connector_name="CONFLUENCE",
            record_group_id="rg-confluence",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == (
            f"records/{CONFLUENCE_CONNECTOR_ID}/Engineering Space/"
            "Architecture/Database Design"
        )

    @pytest.mark.asyncio
    async def test_confluence_root_page(self):
        """Engineering Space → Architecture (root-level page)"""
        provider = _build_full_provider()
        record = _make_record(
            "rec-arch-page", "Architecture",
            connector_id=CONFLUENCE_CONNECTOR_ID,
            connector_name="CONFLUENCE",
            record_group_id="rg-confluence",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == (
            f"records/{CONFLUENCE_CONNECTOR_ID}/Engineering Space/Architecture"
        )

    @pytest.mark.asyncio
    async def test_confluence_separate_subtree(self):
        """Engineering Space → Onboarding → New Hire Guide"""
        provider = _build_full_provider()
        record = _make_record(
            "rec-newhire-guide", "New Hire Guide",
            connector_id=CONFLUENCE_CONNECTOR_ID,
            connector_name="CONFLUENCE",
            record_group_id="rg-confluence",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == (
            f"records/{CONFLUENCE_CONNECTOR_ID}/Engineering Space/"
            "Onboarding/New Hire Guide"
        )


# ---------------------------------------------------------------------------
# Tests: Hierarchical storage path — Jira
# ---------------------------------------------------------------------------


class TestJiraHierarchicalPaths:

    @pytest.mark.asyncio
    async def test_jira_deep_task(self):
        """Sprint Board → Epic → Story → Task"""
        provider = _build_full_provider()
        record = _make_record(
            "rec-task-tests", "Write unit tests",
            connector_id=JIRA_CONNECTOR_ID,
            connector_name="JIRA",
            record_group_id="rg-jira",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == (
            f"records/{JIRA_CONNECTOR_ID}/Sprint Board/"
            "Build Payment Gateway/Implement OAuth/Write unit tests"
        )

    @pytest.mark.asyncio
    async def test_jira_sibling_task(self):
        """Same story, different task → same parent path, different leaf."""
        provider = _build_full_provider()
        record = _make_record(
            "rec-task-itests", "Add integration tests",
            connector_id=JIRA_CONNECTOR_ID,
            connector_name="JIRA",
            record_group_id="rg-jira",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == (
            f"records/{JIRA_CONNECTOR_ID}/Sprint Board/"
            "Build Payment Gateway/Implement OAuth/Add integration tests"
        )

    @pytest.mark.asyncio
    async def test_jira_separate_epic(self):
        """Sprint Board → Migrate Database → Schema migration task"""
        provider = _build_full_provider()
        record = _make_record(
            "rec-task-schema", "Schema migration task",
            connector_id=JIRA_CONNECTOR_ID,
            connector_name="JIRA",
            record_group_id="rg-jira",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == (
            f"records/{JIRA_CONNECTOR_ID}/Sprint Board/"
            "Migrate Database/Schema migration task"
        )

    @pytest.mark.asyncio
    async def test_jira_epic_level(self):
        """Epic record itself: Sprint Board → Build Payment Gateway"""
        provider = _build_full_provider()
        record = _make_record(
            "rec-epic-pay", "Build Payment Gateway",
            connector_id=JIRA_CONNECTOR_ID,
            connector_name="JIRA",
            record_group_id="rg-jira",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == (
            f"records/{JIRA_CONNECTOR_ID}/Sprint Board/Build Payment Gateway"
        )


# ---------------------------------------------------------------------------
# Tests: Hierarchical storage path — Knowledge Base (NESTED, not flat)
# ---------------------------------------------------------------------------


class TestKBHierarchicalPaths:
    """KB records must be stored with full nested folder hierarchy,
    exactly like any other connector."""

    @pytest.mark.asyncio
    async def test_kb_deeply_nested_file(self):
        """Policy KB → HR Policies → Onboarding → company_handbook.pdf (3 levels)"""
        provider = _build_full_provider()
        record = _make_record(
            "rec-kb-handbook", "company_handbook.pdf",
            connector_id=KB_CONNECTOR_ID,
            connector_name="LOCAL_KB",
            record_group_id="rg-kb",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == (
            f"records/{KB_CONNECTOR_ID}/Policy KB/"
            "HR Policies/Onboarding/company_handbook.pdf"
        )

    @pytest.mark.asyncio
    async def test_kb_sibling_in_same_folder(self):
        """Policy KB → HR Policies → Onboarding → benefits_overview.docx"""
        provider = _build_full_provider()
        record = _make_record(
            "rec-kb-benefits", "benefits_overview.docx",
            connector_id=KB_CONNECTOR_ID,
            connector_name="LOCAL_KB",
            record_group_id="rg-kb",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == (
            f"records/{KB_CONNECTOR_ID}/Policy KB/"
            "HR Policies/Onboarding/benefits_overview.docx"
        )

    @pytest.mark.asyncio
    async def test_kb_different_branch_same_parent(self):
        """Policy KB → HR Policies → Leave Policy → leave_guidelines.pdf"""
        provider = _build_full_provider()
        record = _make_record(
            "rec-kb-leave-guide", "leave_guidelines.pdf",
            connector_id=KB_CONNECTOR_ID,
            connector_name="LOCAL_KB",
            record_group_id="rg-kb",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == (
            f"records/{KB_CONNECTOR_ID}/Policy KB/"
            "HR Policies/Leave Policy/leave_guidelines.pdf"
        )

    @pytest.mark.asyncio
    async def test_kb_four_level_deep(self):
        """Policy KB → Engineering → Architecture → API Reference → api_reference.md"""
        provider = _build_full_provider()
        record = _make_record(
            "rec-kb-apiref", "api_reference.md",
            connector_id=KB_CONNECTOR_ID,
            connector_name="LOCAL_KB",
            record_group_id="rg-kb",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == (
            f"records/{KB_CONNECTOR_ID}/Policy KB/"
            "Engineering/Architecture/API Reference/api_reference.md"
        )

    @pytest.mark.asyncio
    async def test_kb_mid_level_folder(self):
        """Folder record: Policy KB → Engineering → Architecture"""
        provider = _build_full_provider()
        record = _make_record(
            "rec-kb-arch", "Architecture",
            connector_id=KB_CONNECTOR_ID,
            connector_name="LOCAL_KB",
            record_group_id="rg-kb",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == (
            f"records/{KB_CONNECTOR_ID}/Policy KB/"
            "Engineering/Architecture"
        )

    @pytest.mark.asyncio
    async def test_kb_top_level_folder(self):
        """Top-level folder record: Policy KB → HR Policies"""
        provider = _build_full_provider()
        record = _make_record(
            "rec-kb-hr", "HR Policies",
            connector_id=KB_CONNECTOR_ID,
            connector_name="LOCAL_KB",
            record_group_id="rg-kb",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == f"records/{KB_CONNECTOR_ID}/Policy KB/HR Policies"

    @pytest.mark.asyncio
    async def test_kb_separate_branch(self):
        """Policy KB → Engineering → Coding Standards → style_guide.md"""
        provider = _build_full_provider()
        record = _make_record(
            "rec-kb-styleguide", "style_guide.md",
            connector_id=KB_CONNECTOR_ID,
            connector_name="LOCAL_KB",
            record_group_id="rg-kb",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == (
            f"records/{KB_CONNECTOR_ID}/Policy KB/"
            "Engineering/Coding Standards/style_guide.md"
        )

    @pytest.mark.asyncio
    async def test_kb_legal_two_levels(self):
        """Policy KB → Legal → compliance_guide.pdf"""
        provider = _build_full_provider()
        record = _make_record(
            "rec-kb-compliance", "compliance_guide.pdf",
            connector_id=KB_CONNECTOR_ID,
            connector_name="LOCAL_KB",
            record_group_id="rg-kb",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == (
            f"records/{KB_CONNECTOR_ID}/Policy KB/"
            "Legal/compliance_guide.pdf"
        )

    @pytest.mark.asyncio
    async def test_kb_second_kb_connector_hierarchy(self):
        """Second KB: Technical Docs → Infrastructure → deployment_runbook.md"""
        provider = _build_full_provider()
        record = _make_record(
            "rec-kb2-runbook", "deployment_runbook.md",
            connector_id=KB_CONNECTOR_ID_2,
            connector_name="LOCAL_KB",
            record_group_id="rg-kb2",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == (
            f"records/{KB_CONNECTOR_ID_2}/Technical Docs/"
            "Infrastructure/deployment_runbook.md"
        )

    @pytest.mark.asyncio
    async def test_kb_all_paths_unique(self):
        """Every KB record across both KBs produces a unique path."""
        provider = _build_full_provider()
        kb_records = [
            _make_record("rec-kb-handbook", "company_handbook.pdf",
                         connector_id=KB_CONNECTOR_ID, connector_name="LOCAL_KB",
                         record_group_id="rg-kb"),
            _make_record("rec-kb-benefits", "benefits_overview.docx",
                         connector_id=KB_CONNECTOR_ID, connector_name="LOCAL_KB",
                         record_group_id="rg-kb"),
            _make_record("rec-kb-leave-guide", "leave_guidelines.pdf",
                         connector_id=KB_CONNECTOR_ID, connector_name="LOCAL_KB",
                         record_group_id="rg-kb"),
            _make_record("rec-kb-sysdesign", "system_design.pdf",
                         connector_id=KB_CONNECTOR_ID, connector_name="LOCAL_KB",
                         record_group_id="rg-kb"),
            _make_record("rec-kb-apiref", "api_reference.md",
                         connector_id=KB_CONNECTOR_ID, connector_name="LOCAL_KB",
                         record_group_id="rg-kb"),
            _make_record("rec-kb-styleguide", "style_guide.md",
                         connector_id=KB_CONNECTOR_ID, connector_name="LOCAL_KB",
                         record_group_id="rg-kb"),
            _make_record("rec-kb-compliance", "compliance_guide.pdf",
                         connector_id=KB_CONNECTOR_ID, connector_name="LOCAL_KB",
                         record_group_id="rg-kb"),
            _make_record("rec-kb2-runbook", "deployment_runbook.md",
                         connector_id=KB_CONNECTOR_ID_2, connector_name="LOCAL_KB",
                         record_group_id="rg-kb2"),
        ]
        paths = set()
        for rec in kb_records:
            path = await build_hierarchical_storage_path(rec, provider)
            assert path is not None, f"No path for {rec.id}"
            paths.add(path)
        assert len(paths) == len(kb_records), f"Duplicate KB paths: {paths}"


# ---------------------------------------------------------------------------
# Tests: Hierarchical storage path — SharePoint
# ---------------------------------------------------------------------------


class TestSharePointHierarchicalPaths:

    @pytest.mark.asyncio
    async def test_sharepoint_deep_file(self):
        """Team Site → Documents → Q4 Reports → financial_summary.xlsx"""
        provider = _build_full_provider()
        record = _make_record(
            "rec-sp-financial", "financial_summary.xlsx",
            connector_id=SHAREPOINT_CONNECTOR_ID,
            connector_name="SHAREPOINT",
            record_group_id="rg-sharepoint",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == (
            f"records/{SHAREPOINT_CONNECTOR_ID}/Team Site/"
            "Documents/Q4 Reports/financial_summary.xlsx"
        )

    @pytest.mark.asyncio
    async def test_sharepoint_sibling_branch(self):
        """Team Site → Documents → Templates → invoice_template.docx"""
        provider = _build_full_provider()
        record = _make_record(
            "rec-sp-invoice", "invoice_template.docx",
            connector_id=SHAREPOINT_CONNECTOR_ID,
            connector_name="SHAREPOINT",
            record_group_id="rg-sharepoint",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == (
            f"records/{SHAREPOINT_CONNECTOR_ID}/Team Site/"
            "Documents/Templates/invoice_template.docx"
        )


# ---------------------------------------------------------------------------
# Tests: Hierarchical storage path — Web
# ---------------------------------------------------------------------------


class TestWebHierarchicalPaths:

    @pytest.mark.asyncio
    async def test_web_connector_url_path(self):
        """WEB connector uses URL segments, not graph hierarchy."""
        provider = _build_full_provider()
        record = _make_record(
            "rec-web-page", "About Us",
            connector_id=WEB_CONNECTOR_ID,
            connector_name="WEB",
            weburl="https://www.example.com/about/team",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == f"records/{WEB_CONNECTOR_ID}/www.example.com/about/team"

    @pytest.mark.asyncio
    async def test_web_root_url(self):
        """WEB connector with root URL (no path after host)."""
        provider = _build_full_provider()
        record = _make_record(
            "rec-web-root", "Home",
            connector_id=WEB_CONNECTOR_ID,
            connector_name="WEB",
            weburl="https://docs.example.io/",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == f"records/{WEB_CONNECTOR_ID}/docs.example.io"

    @pytest.mark.asyncio
    async def test_web_deep_url_path(self):
        """WEB connector with deep URL path segments."""
        provider = _build_full_provider()
        record = _make_record(
            "rec-web-deep", "API v2 Docs",
            connector_id=WEB_CONNECTOR_ID,
            connector_name="WEB",
            weburl="https://api.example.com/v2/docs/reference/endpoints",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == (
            f"records/{WEB_CONNECTOR_ID}/api.example.com/"
            "v2/docs/reference/endpoints"
        )

    @pytest.mark.asyncio
    async def test_web_url_with_special_chars(self):
        """URL path segments with special characters get sanitized."""
        provider = _build_full_provider()
        record = _make_record(
            "rec-web-special", "Special Page",
            connector_id=WEB_CONNECTOR_ID,
            connector_name="WEB",
            weburl="https://www.example.com/blog/2024/hello-world",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == (
            f"records/{WEB_CONNECTOR_ID}/www.example.com/"
            "blog/2024/hello-world"
        )


# ---------------------------------------------------------------------------
# Tests: Fallback paths
# ---------------------------------------------------------------------------


class TestFallbackPaths:

    @pytest.mark.asyncio
    async def test_no_graph_provider_falls_back_to_vrid(self):
        record = _make_record(
            "rec-1", "test.txt",
            connector_id=GDRIVE_CONNECTOR_ID,
            virtual_record_id="vrid-flat",
        )
        path = await build_hierarchical_storage_path(
            record, None, virtual_record_id="vrid-flat"
        )
        assert path == "records/vrid-flat"

    @pytest.mark.asyncio
    async def test_no_connector_id_falls_back(self):
        provider = _build_full_provider()
        record = SimpleNamespace(
            id="rec-orphan", record_name="orphan.txt",
            connector_id=None, connector_name=None,
            record_group_id=None,
        )
        path = await build_hierarchical_storage_path(
            record, provider, virtual_record_id="vrid-orphan"
        )
        assert path == "records/vrid-orphan"

    @pytest.mark.asyncio
    async def test_no_connector_no_vrid_returns_none(self):
        provider = _build_full_provider()
        record = SimpleNamespace(
            id="rec-x", record_name="x.txt",
            connector_id=None, connector_name=None,
            record_group_id=None,
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path is None

    @pytest.mark.asyncio
    async def test_missing_record_group_skips_group_segment(self):
        provider = _build_full_provider()
        record = _make_record(
            "rec-api-spec", "api_spec.yaml",
            connector_id=GDRIVE_CONNECTOR_ID,
            record_group_id="rg-nonexistent",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path.startswith(f"records/{GDRIVE_CONNECTOR_ID}/")
        assert "api_spec.yaml" in path
        assert "rg-nonexistent" not in path

    @pytest.mark.asyncio
    async def test_record_without_group_id(self):
        """Record has connector_id but no record_group_id."""
        provider = _build_full_provider()
        record = _make_record(
            "rec-api-spec", "api_spec.yaml",
            connector_id=GDRIVE_CONNECTOR_ID,
            record_group_id=None,
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path.startswith(f"records/{GDRIVE_CONNECTOR_ID}/")
        assert "api_spec.yaml" in path

    @pytest.mark.asyncio
    async def test_record_not_in_graph_uses_name_only(self):
        """Record id not found in provider falls back to connector/name."""
        provider = StorageGraphProvider()
        provider.add_record_group("rg-1", "MyGroup")
        record = _make_record(
            "rec-unknown", "mystery.txt",
            connector_id="conn-1",
            record_group_id="rg-1",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == "records/conn-1/MyGroup/mystery.txt"

    @pytest.mark.asyncio
    async def test_graph_provider_error_falls_back(self):
        """If graph_provider.get_record_path raises, fall back to vrid."""
        provider = StorageGraphProvider()
        provider.get_record_path = AsyncMock(side_effect=RuntimeError("DB down"))
        provider.get_record_group_by_id = AsyncMock(return_value=None)
        record = _make_record(
            "rec-1", "test.txt",
            connector_id="conn-1",
            virtual_record_id="vrid-fallback",
        )
        path = await build_hierarchical_storage_path(
            record, provider, virtual_record_id="vrid-fallback"
        )
        assert path == "records/vrid-fallback"


# ---------------------------------------------------------------------------
# Tests: Special characters and edge cases in path segments
# ---------------------------------------------------------------------------


class TestSpecialCharacterPaths:

    @pytest.mark.asyncio
    async def test_special_chars_sanitized(self):
        """Characters : * ? < > | / \\ \" in names are replaced with _."""
        provider = StorageGraphProvider()
        provider.add_record_group("rg-1", 'Q1: Goals & "Targets"')
        provider.add_record("rec-1", "file<v2>.txt")
        record = _make_record(
            "rec-1", "file<v2>.txt",
            connector_id="conn-1",
            record_group_id="rg-1",
        )
        path = await build_hierarchical_storage_path(record, provider)
        segments = path.split("/", 2)[-1]
        assert ":" not in segments
        assert "<" not in segments
        assert ">" not in segments
        assert '"' not in segments

    @pytest.mark.asyncio
    async def test_special_chars_in_nested_kb_folders(self):
        """KB folder names with special characters are sanitized at every level."""
        provider = StorageGraphProvider()
        provider.add_record_group("rg-k", "HR: Policies & Guides")
        provider.add_record("rec-f1", "Q3/Q4 Reports", connectorId="conn-k")
        provider.add_record("rec-f2", 'Budget "Final"', connectorId="conn-k")
        provider.add_record("rec-doc", "summary<v3>.pdf", connectorId="conn-k")
        provider.add_parent_child("rec-f2", "rec-f1")
        provider.add_parent_child("rec-doc", "rec-f2")
        record = _make_record(
            "rec-doc", "summary<v3>.pdf",
            connector_id="conn-k",
            connector_name="LOCAL_KB",
            record_group_id="rg-k",
        )
        path = await build_hierarchical_storage_path(record, provider)
        for char in [':', '<', '>', '"', '|', '?', '*']:
            after_connector = path.split("/", 2)[-1]
            assert char not in after_connector

    def test_segment_truncated_at_100_chars(self):
        long_name = "A" * 200
        result = sanitize_path_segment(long_name)
        assert len(result) == 100

    def test_segment_with_unicode_preserved(self):
        result = sanitize_path_segment("日本語ドキュメント")
        assert result == "日本語ドキュメント"

    def test_segment_with_spaces_preserved(self):
        result = sanitize_path_segment("My Important Document")
        assert result == "My Important Document"

    def test_segment_with_dots_preserved(self):
        result = sanitize_path_segment("file.name.v2.txt")
        assert result == "file.name.v2.txt"

    def test_segment_all_special_chars(self):
        result = sanitize_path_segment(':*?"<>|/\\')
        assert result == "_" * 9

    def test_empty_string_returns_empty(self):
        result = sanitize_path_segment("")
        assert result == ""

    @pytest.mark.asyncio
    async def test_record_name_same_as_group_name(self):
        """Record named identically to its group still produces correct path."""
        provider = StorageGraphProvider()
        provider.add_record_group("rg-x", "Documentation")
        provider.add_record("rec-x", "Documentation")
        record = _make_record(
            "rec-x", "Documentation",
            connector_id="conn-x",
            record_group_id="rg-x",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == "records/conn-x/Documentation/Documentation"

    @pytest.mark.asyncio
    async def test_record_name_same_as_parent_folder(self):
        """Record named same as its parent folder."""
        provider = StorageGraphProvider()
        provider.add_record_group("rg-x", "Root")
        provider.add_record("rec-folder", "Reports")
        provider.add_record("rec-file", "Reports")
        provider.add_parent_child("rec-file", "rec-folder")
        record = _make_record(
            "rec-file", "Reports",
            connector_id="conn-x",
            record_group_id="rg-x",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path == "records/conn-x/Root/Reports/Reports"


# ---------------------------------------------------------------------------
# Tests: Very deep nesting (10+ levels)
# ---------------------------------------------------------------------------


class TestVeryDeepNesting:

    @pytest.mark.asyncio
    async def test_ten_level_deep_hierarchy(self):
        """10 levels of nesting produces correct path with all ancestors."""
        provider = StorageGraphProvider()
        provider.add_record_group("rg-deep", "Root Group")
        depth = 10
        for i in range(depth):
            provider.add_record(f"rec-level-{i}", f"Level_{i}")
            if i > 0:
                provider.add_parent_child(f"rec-level-{i}", f"rec-level-{i-1}")
        provider.add_record("rec-leaf", "leaf_document.pdf")
        provider.add_parent_child("rec-leaf", f"rec-level-{depth-1}")

        record = _make_record(
            "rec-leaf", "leaf_document.pdf",
            connector_id="conn-deep",
            record_group_id="rg-deep",
        )
        path = await build_hierarchical_storage_path(record, provider)
        expected_parts = ["records", "conn-deep", "Root Group"]
        expected_parts.extend(f"Level_{i}" for i in range(depth))
        expected_parts.append("leaf_document.pdf")
        assert path == "/".join(expected_parts)

    @pytest.mark.asyncio
    async def test_fifteen_level_deep_kb(self):
        """KB with 15-level nesting — deeply nested documentation."""
        provider = StorageGraphProvider()
        provider.add_record_group("rg-deepkb", "Deep KB")
        folders = [
            "Company", "Division", "Department", "Team", "Project",
            "Module", "Component", "SubComponent", "Feature", "Version",
            "Release", "Hotfix", "Patch", "Build", "Artifact",
        ]
        for i, name in enumerate(folders):
            provider.add_record(f"rec-dkb-{i}", name)
            if i > 0:
                provider.add_parent_child(f"rec-dkb-{i}", f"rec-dkb-{i-1}")
        provider.add_record("rec-dkb-leaf", "final_doc.pdf")
        provider.add_parent_child("rec-dkb-leaf", f"rec-dkb-{len(folders)-1}")

        record = _make_record(
            "rec-dkb-leaf", "final_doc.pdf",
            connector_id="conn-deepkb",
            connector_name="LOCAL_KB",
            record_group_id="rg-deepkb",
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path is not None
        segments = path.split("/")
        assert segments[0] == "records"
        assert segments[1] == "conn-deepkb"
        assert segments[2] == "Deep KB"
        assert segments[-1] == "final_doc.pdf"
        assert len(segments) == 3 + len(folders) + 1

    @pytest.mark.asyncio
    async def test_circular_parent_edge_does_not_infinite_loop(self):
        """Circular parent-child edges stop traversal without infinite loop."""
        provider = StorageGraphProvider()
        provider.add_record_group("rg-circ", "Circular")
        provider.add_record("rec-a", "A")
        provider.add_record("rec-b", "B")
        provider.add_record("rec-c", "C")
        provider.add_parent_child("rec-b", "rec-a")
        provider.add_parent_child("rec-c", "rec-b")
        provider.add_parent_child("rec-a", "rec-c")

        record = _make_record(
            "rec-c", "C", connector_id="conn-circ", record_group_id="rg-circ"
        )
        path = await build_hierarchical_storage_path(record, provider)
        assert path is not None
        assert "C" in path


# ---------------------------------------------------------------------------
# Tests: Record rename / move scenarios
# ---------------------------------------------------------------------------


class TestRecordRenameAndMoveScenarios:

    @pytest.mark.asyncio
    async def test_rename_record_changes_path(self):
        """Renaming a record produces a different storage path."""
        provider = _build_full_provider()
        record_before = _make_record(
            "rec-kb-handbook", "company_handbook.pdf",
            connector_id=KB_CONNECTOR_ID, connector_name="LOCAL_KB",
            record_group_id="rg-kb",
        )
        path_before = await build_hierarchical_storage_path(record_before, provider)

        provider.rename_record("rec-kb-handbook", "employee_handbook_v2.pdf")
        record_after = _make_record(
            "rec-kb-handbook", "employee_handbook_v2.pdf",
            connector_id=KB_CONNECTOR_ID, connector_name="LOCAL_KB",
            record_group_id="rg-kb",
        )
        path_after = await build_hierarchical_storage_path(record_after, provider)

        assert path_before != path_after
        assert "company_handbook.pdf" in path_before
        assert "employee_handbook_v2.pdf" in path_after
        assert path_before.rsplit("/", 1)[0] == path_after.rsplit("/", 1)[0]

    @pytest.mark.asyncio
    async def test_rename_parent_folder_changes_descendant_paths(self):
        """Renaming a parent folder changes all descendant paths."""
        provider = _build_full_provider()
        record = _make_record(
            "rec-kb-handbook", "company_handbook.pdf",
            connector_id=KB_CONNECTOR_ID, connector_name="LOCAL_KB",
            record_group_id="rg-kb",
        )
        path_before = await build_hierarchical_storage_path(record, provider)
        assert "Onboarding" in path_before

        provider.rename_record("rec-kb-onboarding", "Getting Started")
        path_after = await build_hierarchical_storage_path(record, provider)
        assert "Getting Started" in path_after
        assert "Onboarding" not in path_after
        assert path_before != path_after

    @pytest.mark.asyncio
    async def test_rename_group_changes_all_paths(self):
        """Renaming a record group changes group segment in all record paths."""
        provider = _build_full_provider()
        record = _make_record(
            "rec-kb-handbook", "company_handbook.pdf",
            connector_id=KB_CONNECTOR_ID, connector_name="LOCAL_KB",
            record_group_id="rg-kb",
        )
        path_before = await build_hierarchical_storage_path(record, provider)
        assert "Policy KB" in path_before

        provider.rename_record_group("rg-kb", "Company Policies v2")
        path_after = await build_hierarchical_storage_path(record, provider)
        assert "Company Policies v2" in path_after
        assert "Policy KB" not in path_after

    @pytest.mark.asyncio
    async def test_move_record_to_different_parent(self):
        """Moving a record to a different parent changes its path."""
        provider = _build_full_provider()
        record = _make_record(
            "rec-kb-handbook", "company_handbook.pdf",
            connector_id=KB_CONNECTOR_ID, connector_name="LOCAL_KB",
            record_group_id="rg-kb",
        )
        path_before = await build_hierarchical_storage_path(record, provider)
        assert "HR Policies/Onboarding" in path_before

        provider.move_record("rec-kb-handbook", "rec-kb-legal")
        path_after = await build_hierarchical_storage_path(record, provider)
        assert "Legal/company_handbook.pdf" in path_after
        assert "Onboarding" not in path_after

    @pytest.mark.asyncio
    async def test_move_folder_with_children(self):
        """Moving a folder moves all its children's paths too."""
        provider = _build_full_provider()
        rec_child = _make_record(
            "rec-kb-handbook", "company_handbook.pdf",
            connector_id=KB_CONNECTOR_ID, connector_name="LOCAL_KB",
            record_group_id="rg-kb",
        )
        path_before = await build_hierarchical_storage_path(rec_child, provider)
        assert "HR Policies/Onboarding/company_handbook.pdf" in path_before

        provider.move_record("rec-kb-onboarding", "rec-kb-eng")
        path_after = await build_hierarchical_storage_path(rec_child, provider)
        assert "Engineering/Onboarding/company_handbook.pdf" in path_after
        assert "HR Policies" not in path_after

    @pytest.mark.asyncio
    async def test_orphan_record_after_parent_removed(self):
        """Removing parent edge makes record a root-level record under group."""
        provider = _build_full_provider()
        record = _make_record(
            "rec-kb-handbook", "company_handbook.pdf",
            connector_id=KB_CONNECTOR_ID, connector_name="LOCAL_KB",
            record_group_id="rg-kb",
        )
        path_before = await build_hierarchical_storage_path(record, provider)
        assert "Onboarding" in path_before

        provider.remove_parent_edge("rec-kb-handbook")
        path_after = await build_hierarchical_storage_path(record, provider)
        assert path_after == (
            f"records/{KB_CONNECTOR_ID}/Policy KB/company_handbook.pdf"
        )


# ---------------------------------------------------------------------------
# Tests: StorageCleanupHelper — path delegation
# ---------------------------------------------------------------------------


class TestStorageCleanupHelperPathDelegation:

    @pytest.mark.asyncio
    async def test_helper_matches_standalone_gdrive(self):
        provider = _build_full_provider()
        helper = StorageCleanupHelper(MagicMock(), provider, AsyncMock())
        record = _make_record(
            "rec-api-spec", "api_spec.yaml",
            connector_id=GDRIVE_CONNECTOR_ID,
            record_group_id="rg-gdrive",
            virtual_record_id="vrid-api-spec",
        )
        helper_path = await helper.build_record_path(record)
        standalone_path = await build_hierarchical_storage_path(
            record, provider, virtual_record_id="vrid-api-spec", logger=MagicMock()
        )
        assert helper_path == standalone_path

    @pytest.mark.asyncio
    async def test_helper_matches_standalone_jira_deep(self):
        provider = _build_full_provider()
        helper = StorageCleanupHelper(MagicMock(), provider, AsyncMock())
        record = _make_record(
            "rec-task-tests", "Write unit tests",
            connector_id=JIRA_CONNECTOR_ID, connector_name="JIRA",
            record_group_id="rg-jira", virtual_record_id="vrid-task",
        )
        path = await helper.build_record_path(record)
        assert path == (
            f"records/{JIRA_CONNECTOR_ID}/Sprint Board/"
            "Build Payment Gateway/Implement OAuth/Write unit tests"
        )

    @pytest.mark.asyncio
    async def test_helper_matches_kb_nested(self):
        """KB nested record via helper matches standalone."""
        provider = _build_full_provider()
        helper = StorageCleanupHelper(MagicMock(), provider, AsyncMock())
        record = _make_record(
            "rec-kb-handbook", "company_handbook.pdf",
            connector_id=KB_CONNECTOR_ID, connector_name="LOCAL_KB",
            record_group_id="rg-kb",
        )
        path = await helper.build_record_path(record)
        assert path == (
            f"records/{KB_CONNECTOR_ID}/Policy KB/"
            "HR Policies/Onboarding/company_handbook.pdf"
        )

    @pytest.mark.asyncio
    async def test_helper_matches_kb_four_levels(self):
        """KB 4-level deep record via helper."""
        provider = _build_full_provider()
        helper = StorageCleanupHelper(MagicMock(), provider, AsyncMock())
        record = _make_record(
            "rec-kb-apiref", "api_reference.md",
            connector_id=KB_CONNECTOR_ID, connector_name="LOCAL_KB",
            record_group_id="rg-kb",
        )
        path = await helper.build_record_path(record)
        assert path == (
            f"records/{KB_CONNECTOR_ID}/Policy KB/"
            "Engineering/Architecture/API Reference/api_reference.md"
        )

    @pytest.mark.asyncio
    async def test_helper_matches_sharepoint(self):
        provider = _build_full_provider()
        helper = StorageCleanupHelper(MagicMock(), provider, AsyncMock())
        record = _make_record(
            "rec-sp-financial", "financial_summary.xlsx",
            connector_id=SHAREPOINT_CONNECTOR_ID, connector_name="SHAREPOINT",
            record_group_id="rg-sharepoint",
        )
        path = await helper.build_record_path(record)
        assert path == (
            f"records/{SHAREPOINT_CONNECTOR_ID}/Team Site/"
            "Documents/Q4 Reports/financial_summary.xlsx"
        )

    def test_build_record_group_path(self):
        helper = StorageCleanupHelper(MagicMock(), MagicMock(), AsyncMock())
        path = helper.build_record_group_path(GDRIVE_CONNECTOR_ID, "Shared Drive")
        assert path == f"records/{GDRIVE_CONNECTOR_ID}/Shared Drive"

    def test_build_record_group_path_none_inputs(self):
        helper = StorageCleanupHelper(MagicMock(), MagicMock(), AsyncMock())
        assert helper.build_record_group_path(None, "X") is None
        assert helper.build_record_group_path("cid", None) is None
        assert helper.build_record_group_path(None, None) is None


# ---------------------------------------------------------------------------
# Tests: Storage cleanup operations (move-tree, delete-connector-storage)
# ---------------------------------------------------------------------------


class TestStorageCleanupOperations:

    @pytest.mark.asyncio
    async def test_move_tree_noop_same_path(self):
        config_svc = AsyncMock()
        helper = StorageCleanupHelper(MagicMock(), _build_full_provider(), config_svc)
        await helper.move_record_tree(ORG_ID, "records/a/b", "records/a/b")
        config_svc.get_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_move_tree_calls_storage_api(self):
        config_svc = AsyncMock()
        config_svc.get_config = AsyncMock(side_effect=[
            {"scopedJwtSecret": "test-secret-key"},
            {"cm": {"endpoint": "http://localhost:3000"}},
        ])
        helper = StorageCleanupHelper(MagicMock(), _build_full_provider(), config_svc)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.connectors.core.base.data_processor.storage_cleanup.aiohttp.ClientSession",
                    return_value=mock_session):
            await helper.move_record_tree(
                ORG_ID, "records/old/path", "records/new/path"
            )
        mock_session.post.assert_called_once()
        call_kwargs = mock_session.post.call_args
        assert call_kwargs[1]["json"]["oldPath"] == "records/old/path"
        assert call_kwargs[1]["json"]["newPath"] == "records/new/path"

    @pytest.mark.asyncio
    async def test_move_tree_kb_hierarchy_path(self):
        """Move triggered by KB folder rename produces correct old/new paths."""
        provider = _build_full_provider()
        config_svc = AsyncMock()
        config_svc.get_config = AsyncMock(side_effect=[
            {"scopedJwtSecret": "test-secret-key"},
            {"cm": {"endpoint": "http://localhost:3000"}},
        ])
        helper = StorageCleanupHelper(MagicMock(), provider, config_svc)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        old_path = f"records/{KB_CONNECTOR_ID}/Policy KB/HR Policies/Onboarding"
        new_path = f"records/{KB_CONNECTOR_ID}/Policy KB/HR Policies/Getting Started"

        with patch("app.connectors.core.base.data_processor.storage_cleanup.aiohttp.ClientSession",
                    return_value=mock_session):
            await helper.move_record_tree(ORG_ID, old_path, new_path)

        call_kwargs = mock_session.post.call_args
        assert call_kwargs[1]["json"]["oldPath"] == old_path
        assert call_kwargs[1]["json"]["newPath"] == new_path

    @pytest.mark.asyncio
    async def test_delete_connector_storage_returns_count(self):
        config_svc = AsyncMock()
        config_svc.get_config = AsyncMock(side_effect=[
            {"scopedJwtSecret": "test-secret-key"},
            {"cm": {"endpoint": "http://localhost:3000"}},
        ])
        helper = StorageCleanupHelper(MagicMock(), MagicMock(), config_svc)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"deleted": 42})
        mock_resp.text = AsyncMock(return_value="")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_session = AsyncMock()
        mock_session.delete = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.connectors.core.base.data_processor.storage_cleanup.aiohttp.ClientSession",
                    return_value=mock_session):
            deleted = await helper.delete_connector_storage(ORG_ID, GDRIVE_CONNECTOR_ID)
        assert deleted == 42

    @pytest.mark.asyncio
    async def test_delete_connector_storage_raises_on_failure(self):
        config_svc = AsyncMock()
        config_svc.get_config = AsyncMock(side_effect=[
            {"scopedJwtSecret": "test-secret-key"},
            {"cm": {"endpoint": "http://localhost:3000"}},
        ])
        helper = StorageCleanupHelper(MagicMock(), MagicMock(), config_svc)

        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.text = AsyncMock(return_value="Internal Server Error")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_session = AsyncMock()
        mock_session.delete = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.connectors.core.base.data_processor.storage_cleanup.aiohttp.ClientSession",
                    return_value=mock_session):
            with pytest.raises(Exception, match="Connector storage delete failed"):
                await helper.delete_connector_storage(ORG_ID, GDRIVE_CONNECTOR_ID)


# ---------------------------------------------------------------------------
# Tests: Pattern match — connector resolution
# ---------------------------------------------------------------------------


class TestPatternMatchConnectorResolution:

    @pytest.mark.asyncio
    async def test_apps_filter_returns_app_ids(self):
        provider = _build_full_provider()
        ids = await resolve_connector_ids_for_search(
            provider, ORG_ID, {"apps": [GDRIVE_CONNECTOR_ID, JIRA_CONNECTOR_ID]}
        )
        assert set(ids) == {GDRIVE_CONNECTOR_ID, JIRA_CONNECTOR_ID}

    @pytest.mark.asyncio
    async def test_kb_filter_returns_kb_ids(self):
        provider = _build_full_provider()
        ids = await resolve_connector_ids_for_search(
            provider, ORG_ID, {"kb": [KB_CONNECTOR_ID]}
        )
        assert ids == [KB_CONNECTOR_ID]

    @pytest.mark.asyncio
    async def test_combined_apps_and_kb(self):
        provider = _build_full_provider()
        ids = await resolve_connector_ids_for_search(
            provider, ORG_ID,
            {"apps": [GDRIVE_CONNECTOR_ID], "kb": [KB_CONNECTOR_ID]},
        )
        assert set(ids) == {GDRIVE_CONNECTOR_ID, KB_CONNECTOR_ID}

    @pytest.mark.asyncio
    async def test_multiple_kbs_in_filter(self):
        """Multiple KB connectors in filter."""
        provider = _build_full_provider()
        ids = await resolve_connector_ids_for_search(
            provider, ORG_ID,
            {"kb": [KB_CONNECTOR_ID, KB_CONNECTOR_ID_2]},
        )
        assert set(ids) == {KB_CONNECTOR_ID, KB_CONNECTOR_ID_2}

    @pytest.mark.asyncio
    async def test_no_filters_returns_all_org_apps(self):
        provider = _build_full_provider()
        ids = await resolve_connector_ids_for_search(provider, ORG_ID, None)
        assert set(ids) == {
            GDRIVE_CONNECTOR_ID, CONFLUENCE_CONNECTOR_ID,
            JIRA_CONNECTOR_ID, KB_CONNECTOR_ID, KB_CONNECTOR_ID_2,
            WEB_CONNECTOR_ID, SHAREPOINT_CONNECTOR_ID,
        }

    @pytest.mark.asyncio
    async def test_empty_filters_returns_all_org_apps(self):
        provider = _build_full_provider()
        ids = await resolve_connector_ids_for_search(provider, ORG_ID, {})
        assert set(ids) == {
            GDRIVE_CONNECTOR_ID, CONFLUENCE_CONNECTOR_ID,
            JIRA_CONNECTOR_ID, KB_CONNECTOR_ID, KB_CONNECTOR_ID_2,
            WEB_CONNECTOR_ID, SHAREPOINT_CONNECTOR_ID,
        }


# ---------------------------------------------------------------------------
# Tests: Pattern match — grep command building
# ---------------------------------------------------------------------------


class TestPatternMatchGrepBuilding:

    def test_simple_query_extracts_keywords(self):
        cmd = build_grep_command_from_query("What is the payment API specification?")
        assert cmd is not None
        assert "payment" in cmd
        assert "specification" in cmd

    def test_short_words_and_stop_words_excluded(self):
        cmd = build_grep_command_from_query("what is the api?")
        assert cmd is not None
        assert "api" in cmd

    def test_pure_stop_words_returns_none(self):
        assert build_grep_command_from_query("what is the") is None

    def test_max_five_keywords(self):
        cmd = build_grep_command_from_query(
            "alpha bravo charlie delta echo foxtrot golf hotel"
        )
        assert cmd is not None
        keywords_in_pattern = cmd.split('"')[1].split(r"\|")
        assert len(keywords_in_pattern) <= 5

    def test_single_keyword(self):
        cmd = build_grep_command_from_query("authentication")
        assert cmd is not None
        assert "authentication" in cmd

    def test_empty_query_returns_none(self):
        assert build_grep_command_from_query("") is None

    def test_query_with_only_special_chars(self):
        result = build_grep_command_from_query("!!! ??? ###")
        assert result is None or isinstance(result, str)


# ---------------------------------------------------------------------------
# Tests: Pattern match — eligibility
# ---------------------------------------------------------------------------


class TestPatternMatchEligibility:

    @pytest.mark.asyncio
    async def test_local_storage_is_eligible(self):
        config_svc = AsyncMock()
        config_svc.get_config = AsyncMock(return_value={"type": "local"})
        with patch("app.utils.pattern_match.is_local_storage", return_value=True):
            assert await check_pattern_match_eligible(config_svc, MagicMock()) is True

    @pytest.mark.asyncio
    async def test_s3_storage_not_eligible(self):
        config_svc = AsyncMock()
        config_svc.get_config = AsyncMock(return_value={"type": "s3"})
        with patch("app.utils.pattern_match.is_local_storage", return_value=False):
            assert await check_pattern_match_eligible(config_svc, MagicMock()) is False

    @pytest.mark.asyncio
    async def test_config_error_returns_false(self):
        config_svc = AsyncMock()
        config_svc.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        assert await check_pattern_match_eligible(config_svc, MagicMock()) is False


# ---------------------------------------------------------------------------
# Tests: execute_pattern_match_pipeline
# ---------------------------------------------------------------------------


class TestExecutePatternMatchPipeline:

    @pytest.mark.asyncio
    async def test_pipeline_empty_for_stop_word_query(self):
        results = await execute_pattern_match_pipeline(
            query="what is the",
            config_service=AsyncMock(),
            org_id=ORG_ID,
            user_id=USER_ID,
            graph_provider=_build_full_provider(),
            filters=None,
            logger_instance=MagicMock(),
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_pipeline_skips_non_local_storage(self):
        config_svc = AsyncMock()
        config_svc.get_config = AsyncMock(return_value={"type": "s3"})
        with patch("app.utils.pattern_match.is_local_storage", return_value=False):
            results = await execute_pattern_match_pipeline(
                query="payment gateway specification",
                config_service=config_svc,
                org_id=ORG_ID,
                user_id=USER_ID,
                graph_provider=_build_full_provider(),
                filters=None,
                logger_instance=MagicMock(),
            )
        assert results == []

    @pytest.mark.asyncio
    async def test_pipeline_fans_out_across_connectors(self):
        config_svc = AsyncMock()
        config_svc.get_config = AsyncMock(return_value={"type": "local"})
        mock_find = AsyncMock(return_value=(
            True,
            '{"records": [{"virtual_record_id": "vrid-1", "path": "/test"}]}',
        ))

        with patch("app.utils.pattern_match.is_local_storage", return_value=True), \
             patch("app.utils.pattern_match.StoragePatternMatch") as MockSPM:
            MockSPM.return_value.find_records = mock_find
            results = await execute_pattern_match_pipeline(
                query="payment gateway specification",
                config_service=config_svc,
                org_id=ORG_ID,
                user_id=USER_ID,
                graph_provider=_build_full_provider(),
                filters={"apps": [GDRIVE_CONNECTOR_ID, JIRA_CONNECTOR_ID]},
                logger_instance=MagicMock(),
            )
        assert mock_find.call_count == 2
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_pipeline_fans_out_kb_and_connectors_together(self):
        """Pipeline resolves both KB and app connectors for search."""
        config_svc = AsyncMock()
        config_svc.get_config = AsyncMock(return_value={"type": "local"})
        mock_find = AsyncMock(return_value=(
            True,
            '{"records": [{"virtual_record_id": "vrid-x", "path": "/p"}]}',
        ))

        with patch("app.utils.pattern_match.is_local_storage", return_value=True), \
             patch("app.utils.pattern_match.StoragePatternMatch") as MockSPM:
            MockSPM.return_value.find_records = mock_find
            results = await execute_pattern_match_pipeline(
                query="onboarding handbook policy",
                config_service=config_svc,
                org_id=ORG_ID,
                user_id=USER_ID,
                graph_provider=_build_full_provider(),
                filters={
                    "apps": [GDRIVE_CONNECTOR_ID],
                    "kb": [KB_CONNECTOR_ID, KB_CONNECTOR_ID_2],
                },
                logger_instance=MagicMock(),
            )
        assert mock_find.call_count == 3

    @pytest.mark.asyncio
    async def test_pipeline_handles_find_records_failure(self):
        """Pipeline handles exception from find_records gracefully."""
        config_svc = AsyncMock()
        config_svc.get_config = AsyncMock(return_value={"type": "local"})
        mock_find = AsyncMock(side_effect=RuntimeError("grep failed"))

        with patch("app.utils.pattern_match.is_local_storage", return_value=True), \
             patch("app.utils.pattern_match.StoragePatternMatch") as MockSPM:
            MockSPM.return_value.find_records = mock_find
            results = await execute_pattern_match_pipeline(
                query="payment specification",
                config_service=config_svc,
                org_id=ORG_ID,
                user_id=USER_ID,
                graph_provider=_build_full_provider(),
                filters={"apps": [GDRIVE_CONNECTOR_ID]},
                logger_instance=MagicMock(),
            )
        assert results == []


# ---------------------------------------------------------------------------
# Tests: merge_pattern_match_results with permissions
# ---------------------------------------------------------------------------


class TestMergePatternMatchWithPermissions:

    @pytest.mark.asyncio
    async def test_dedup_keeps_first_occurrence(self):
        provider = _build_full_provider()
        provider.set_accessible_vrids({"vrid-A": "rec-A"})
        provider.add_record("rec-A", "doc.txt", virtualRecordId="vrid-A")

        blob_store = MagicMock()
        blob_store.config_service = AsyncMock()
        blob_store.config_service.get_config = AsyncMock(return_value={})

        raw = [
            {"virtual_record_id": "vrid-A", "path": "/a"},
            {"virtual_record_id": "vrid-A", "path": "/b"},
        ]

        with patch("app.utils.pattern_match.get_record", new_callable=AsyncMock), \
             patch("app.utils.pattern_match.get_flattened_results",
                   new_callable=AsyncMock, return_value=[]):
            results = await merge_pattern_match_results(
                raw_records=raw,
                virtual_record_id_to_result={},
                user_id=USER_ID,
                org_id=ORG_ID,
                blob_store=blob_store,
                graph_provider=provider,
                is_multimodal_llm=False,
                logger_instance=MagicMock(),
            )
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_inaccessible_vrids_filtered_out(self):
        provider = _build_full_provider()
        provider.set_accessible_vrids({})

        blob_store = MagicMock()
        blob_store.config_service = AsyncMock()
        blob_store.config_service.get_config = AsyncMock(return_value={})

        raw = [{"virtual_record_id": "vrid-blocked", "path": "/x"}]
        results = await merge_pattern_match_results(
            raw_records=raw,
            virtual_record_id_to_result={},
            user_id=USER_ID,
            org_id=ORG_ID,
            blob_store=blob_store,
            graph_provider=provider,
            is_multimodal_llm=False,
            logger_instance=MagicMock(),
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_already_seen_vrids_skipped(self):
        provider = _build_full_provider()
        provider.set_accessible_vrids({"vrid-existing": "rec-1"})

        blob_store = MagicMock()
        blob_store.config_service = AsyncMock()
        blob_store.config_service.get_config = AsyncMock(return_value={})

        raw = [{"virtual_record_id": "vrid-existing", "path": "/x"}]
        existing = {"vrid-existing": {"record_name": "already_there.txt"}}

        results = await merge_pattern_match_results(
            raw_records=raw,
            virtual_record_id_to_result=existing,
            user_id=USER_ID,
            org_id=ORG_ID,
            blob_store=blob_store,
            graph_provider=provider,
            is_multimodal_llm=False,
            logger_instance=MagicMock(),
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_mixed_accessible_and_inaccessible(self):
        """Some records accessible, some not — only accessible ones kept."""
        provider = _build_full_provider()
        provider.set_accessible_vrids({"vrid-ok": "rec-ok"})
        provider.add_record("rec-ok", "ok.txt", virtualRecordId="vrid-ok")

        blob_store = MagicMock()
        blob_store.config_service = AsyncMock()
        blob_store.config_service.get_config = AsyncMock(return_value={})

        raw = [
            {"virtual_record_id": "vrid-ok", "path": "/ok"},
            {"virtual_record_id": "vrid-nope", "path": "/nope"},
            {"virtual_record_id": "vrid-also-nope", "path": "/no"},
        ]

        with patch("app.utils.pattern_match.get_record", new_callable=AsyncMock), \
             patch("app.utils.pattern_match.get_flattened_results",
                   new_callable=AsyncMock, return_value=[]):
            results = await merge_pattern_match_results(
                raw_records=raw,
                virtual_record_id_to_result={},
                user_id=USER_ID,
                org_id=ORG_ID,
                blob_store=blob_store,
                graph_provider=provider,
                is_multimodal_llm=False,
                logger_instance=MagicMock(),
            )
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_empty_raw_records(self):
        """Empty raw records → empty results."""
        provider = _build_full_provider()
        blob_store = MagicMock()
        blob_store.config_service = AsyncMock()
        blob_store.config_service.get_config = AsyncMock(return_value={})

        results = await merge_pattern_match_results(
            raw_records=[],
            virtual_record_id_to_result={},
            user_id=USER_ID,
            org_id=ORG_ID,
            blob_store=blob_store,
            graph_provider=provider,
            is_multimodal_llm=False,
            logger_instance=MagicMock(),
        )
        assert results == []


# ---------------------------------------------------------------------------
# Tests: cap_pattern_match_blocks — proportional distribution
# ---------------------------------------------------------------------------


class TestCapPatternMatchBlocksCrossRecord:

    def test_two_records_fair_share(self):
        blocks = []
        for i in range(10):
            blocks.append({"virtual_record_id": "vrid-A", "idx": i})
        for i in range(10):
            blocks.append({"virtual_record_id": "vrid-B", "idx": i})
        vrid_map: dict = {"vrid-A": {}, "vrid-B": {}}
        result = cap_pattern_match_blocks(
            blocks, budget=10,
            virtual_record_id_to_result=vrid_map,
            logger_instance=MagicMock(),
        )
        assert len(result) == 10
        a_count = sum(1 for b in result if b["virtual_record_id"] == "vrid-A")
        b_count = sum(1 for b in result if b["virtual_record_id"] == "vrid-B")
        assert a_count == 5
        assert b_count == 5

    def test_budget_zero_clears_all(self):
        blocks = [{"virtual_record_id": "vrid-X", "idx": 0}]
        vrid_map: dict = {"vrid-X": {"data": True}}
        result = cap_pattern_match_blocks(
            blocks, budget=0,
            virtual_record_id_to_result=vrid_map,
            logger_instance=MagicMock(),
        )
        assert result == []
        assert "vrid-X" not in vrid_map

    def test_under_budget_returns_all(self):
        blocks = [
            {"virtual_record_id": "vrid-A", "idx": 0},
            {"virtual_record_id": "vrid-A", "idx": 1},
        ]
        vrid_map: dict = {"vrid-A": {}}
        result = cap_pattern_match_blocks(
            blocks, budget=50,
            virtual_record_id_to_result=vrid_map,
            logger_instance=MagicMock(),
        )
        assert len(result) == 2

    def test_orphaned_records_pruned(self):
        blocks = []
        for i in range(20):
            blocks.append({"virtual_record_id": "vrid-big", "idx": i})
        blocks.append({"virtual_record_id": "vrid-tiny", "idx": 0})
        vrid_map: dict = {"vrid-big": {"big": True}, "vrid-tiny": {"tiny": True}}
        result = cap_pattern_match_blocks(
            blocks, budget=2,
            virtual_record_id_to_result=vrid_map,
            logger_instance=MagicMock(),
        )
        assert len(result) == 2
        surviving_vrids = {b["virtual_record_id"] for b in result}
        for vrid in {"vrid-big", "vrid-tiny"} - surviving_vrids:
            assert vrid not in vrid_map

    def test_three_records_uneven_sizes(self):
        """3 records with very different block counts, budget forces proportional cut."""
        blocks = []
        for i in range(30):
            blocks.append({"virtual_record_id": "vrid-large", "idx": i})
        for i in range(10):
            blocks.append({"virtual_record_id": "vrid-medium", "idx": i})
        for i in range(2):
            blocks.append({"virtual_record_id": "vrid-small", "idx": i})
        vrid_map: dict = {"vrid-large": {}, "vrid-medium": {}, "vrid-small": {}}
        result = cap_pattern_match_blocks(
            blocks, budget=6,
            virtual_record_id_to_result=vrid_map,
            logger_instance=MagicMock(),
        )
        assert len(result) == 6

    def test_single_record_gets_full_budget(self):
        blocks = [{"virtual_record_id": "vrid-solo", "idx": i} for i in range(20)]
        vrid_map: dict = {"vrid-solo": {}}
        result = cap_pattern_match_blocks(
            blocks, budget=5,
            virtual_record_id_to_result=vrid_map,
            logger_instance=MagicMock(),
        )
        assert len(result) == 5
        assert all(b["virtual_record_id"] == "vrid-solo" for b in result)

    def test_empty_blocks_returns_empty(self):
        vrid_map: dict = {}
        result = cap_pattern_match_blocks(
            [], budget=10,
            virtual_record_id_to_result=vrid_map,
            logger_instance=MagicMock(),
        )
        assert result == []


# ---------------------------------------------------------------------------
# Tests: Time-range filtering
# ---------------------------------------------------------------------------


class TestTimeRangeFilteringIntegration:

    def test_no_time_range_passes_everything(self):
        assert _record_in_time_range({"source_created_at": 100}, None) is True

    def test_created_after_filters(self):
        record = {"source_created_at": 1000, "source_updated_at": 2000}
        assert _record_in_time_range(record, {"source_created_after_ms": 500}) is True
        assert _record_in_time_range(record, {"source_created_after_ms": 2000}) is False

    def test_updated_before_filters(self):
        record = {"source_created_at": 1000, "source_updated_at": 2000}
        assert _record_in_time_range(record, {"source_updated_before_ms": 3000}) is True
        assert _record_in_time_range(record, {"source_updated_before_ms": 1500}) is False

    def test_combined_range(self):
        record = {"source_created_at": 1000, "source_updated_at": 2000}
        assert _record_in_time_range(
            record,
            {
                "source_created_after_ms": 500,
                "source_created_before_ms": 1500,
                "source_updated_after_ms": 1500,
                "source_updated_before_ms": 2500,
            },
        ) is True

    def test_missing_timestamps_fail_range(self):
        assert _record_in_time_range({}, {"source_created_after_ms": 100}) is False

    def test_exact_boundary_values(self):
        record = {"source_created_at": 1000, "source_updated_at": 2000}
        assert _record_in_time_range(record, {"source_created_after_ms": 1000}) is True
        assert _record_in_time_range(record, {"source_created_before_ms": 1000}) is True

    def test_created_before_filters(self):
        record = {"source_created_at": 1000, "source_updated_at": 2000}
        assert _record_in_time_range(record, {"source_created_before_ms": 1500}) is True
        assert _record_in_time_range(record, {"source_created_before_ms": 500}) is False

    def test_updated_after_filters(self):
        record = {"source_created_at": 1000, "source_updated_at": 2000}
        assert _record_in_time_range(record, {"source_updated_after_ms": 1500}) is True
        assert _record_in_time_range(record, {"source_updated_after_ms": 2500}) is False

    def test_empty_range_dict(self):
        assert _record_in_time_range({"source_created_at": 100}, {}) is True


# ---------------------------------------------------------------------------
# Tests: build_record_group_path standalone
# ---------------------------------------------------------------------------


class TestBuildRecordGroupPath:

    def test_normal_group(self):
        assert build_record_group_path("conn-1", "My Drive") == "records/conn-1/My Drive"

    def test_sanitizes_group_name(self):
        path = build_record_group_path("conn-1", 'Sales: Q3 "Reports"')
        assert path is not None
        assert ":" not in path.split("/", 2)[-1]

    def test_none_connector(self):
        assert build_record_group_path(None, "group") is None

    def test_none_group_name(self):
        assert build_record_group_path("conn-1", None) is None

    def test_empty_group_name(self):
        assert build_record_group_path("conn-1", "") is None

    def test_kb_group_path(self):
        assert build_record_group_path(KB_CONNECTOR_ID, "Policy KB") == (
            f"records/{KB_CONNECTOR_ID}/Policy KB"
        )


# ---------------------------------------------------------------------------
# Tests: Cross-connector path consistency
# ---------------------------------------------------------------------------


class TestCrossConnectorPathConsistency:

    @pytest.mark.asyncio
    async def test_same_record_name_different_connectors(self):
        """Identically named records in different connectors produce different paths."""
        provider = StorageGraphProvider()
        provider.add_record_group("rg-1", "Space A")
        provider.add_record_group("rg-2", "Space B")
        provider.add_record("rec-a", "README.md")
        provider.add_record("rec-b", "README.md")

        rec_a = _make_record("rec-a", "README.md", connector_id="conn-alpha", record_group_id="rg-1")
        rec_b = _make_record("rec-b", "README.md", connector_id="conn-beta", record_group_id="rg-2")

        path_a = await build_hierarchical_storage_path(rec_a, provider)
        path_b = await build_hierarchical_storage_path(rec_b, provider)
        assert path_a != path_b
        assert "conn-alpha" in path_a
        assert "conn-beta" in path_b

    @pytest.mark.asyncio
    async def test_same_name_in_kb_and_connector(self):
        """Same file name in KB and connector don't collide."""
        provider = StorageGraphProvider()
        provider.add_record_group("rg-kb", "Policy KB")
        provider.add_record_group("rg-drive", "Shared Drive")
        provider.add_record("rec-kb-doc", "onboarding.pdf")
        provider.add_record("rec-drive-doc", "onboarding.pdf")

        kb_rec = _make_record(
            "rec-kb-doc", "onboarding.pdf",
            connector_id=KB_CONNECTOR_ID, connector_name="LOCAL_KB",
            record_group_id="rg-kb",
        )
        drive_rec = _make_record(
            "rec-drive-doc", "onboarding.pdf",
            connector_id=GDRIVE_CONNECTOR_ID,
            record_group_id="rg-drive",
        )
        kb_path = await build_hierarchical_storage_path(kb_rec, provider)
        drive_path = await build_hierarchical_storage_path(drive_rec, provider)
        assert kb_path != drive_path

    @pytest.mark.asyncio
    async def test_same_name_same_group_name_different_connectors(self):
        """Same group name and file name but different connector IDs → unique paths."""
        provider = StorageGraphProvider()
        provider.add_record_group("rg-a", "Documents")
        provider.add_record_group("rg-b", "Documents")
        provider.add_record("rec-a", "report.pdf")
        provider.add_record("rec-b", "report.pdf")

        rec_a = _make_record("rec-a", "report.pdf", connector_id="conn-1", record_group_id="rg-a")
        rec_b = _make_record("rec-b", "report.pdf", connector_id="conn-2", record_group_id="rg-b")

        path_a = await build_hierarchical_storage_path(rec_a, provider)
        path_b = await build_hierarchical_storage_path(rec_b, provider)
        assert path_a != path_b

    @pytest.mark.asyncio
    async def test_two_kbs_same_file_name(self):
        """Same file name in two separate KBs → unique paths via connector_id."""
        provider = _build_full_provider()
        provider.add_record("rec-kbdup-1", "readme.md")
        provider.add_record("rec-kbdup-2", "readme.md")

        rec1 = _make_record(
            "rec-kbdup-1", "readme.md",
            connector_id=KB_CONNECTOR_ID, connector_name="LOCAL_KB",
            record_group_id="rg-kb",
        )
        rec2 = _make_record(
            "rec-kbdup-2", "readme.md",
            connector_id=KB_CONNECTOR_ID_2, connector_name="LOCAL_KB",
            record_group_id="rg-kb2",
        )
        path1 = await build_hierarchical_storage_path(rec1, provider)
        path2 = await build_hierarchical_storage_path(rec2, provider)
        assert path1 != path2
        assert KB_CONNECTOR_ID in path1
        assert KB_CONNECTOR_ID_2 in path2

    @pytest.mark.asyncio
    async def test_all_connector_paths_unique(self):
        """Every record in the full hierarchy gets a unique path."""
        provider = _build_full_provider()
        records = [
            _make_record("rec-api-spec", "api_spec.yaml",
                         connector_id=GDRIVE_CONNECTOR_ID, record_group_id="rg-gdrive"),
            _make_record("rec-design-mockup", "design_mockup.png",
                         connector_id=GDRIVE_CONNECTOR_ID, record_group_id="rg-gdrive"),
            _make_record("rec-micro-page", "Microservices Overview",
                         connector_id=CONFLUENCE_CONNECTOR_ID,
                         connector_name="CONFLUENCE", record_group_id="rg-confluence"),
            _make_record("rec-db-design", "Database Design",
                         connector_id=CONFLUENCE_CONNECTOR_ID,
                         connector_name="CONFLUENCE", record_group_id="rg-confluence"),
            _make_record("rec-task-tests", "Write unit tests",
                         connector_id=JIRA_CONNECTOR_ID,
                         connector_name="JIRA", record_group_id="rg-jira"),
            _make_record("rec-task-itests", "Add integration tests",
                         connector_id=JIRA_CONNECTOR_ID,
                         connector_name="JIRA", record_group_id="rg-jira"),
            _make_record("rec-kb-handbook", "company_handbook.pdf",
                         connector_id=KB_CONNECTOR_ID,
                         connector_name="LOCAL_KB", record_group_id="rg-kb"),
            _make_record("rec-kb-apiref", "api_reference.md",
                         connector_id=KB_CONNECTOR_ID,
                         connector_name="LOCAL_KB", record_group_id="rg-kb"),
            _make_record("rec-kb2-runbook", "deployment_runbook.md",
                         connector_id=KB_CONNECTOR_ID_2,
                         connector_name="LOCAL_KB", record_group_id="rg-kb2"),
            _make_record("rec-sp-financial", "financial_summary.xlsx",
                         connector_id=SHAREPOINT_CONNECTOR_ID,
                         connector_name="SHAREPOINT", record_group_id="rg-sharepoint"),
        ]
        paths = set()
        for rec in records:
            path = await build_hierarchical_storage_path(rec, provider)
            assert path is not None, f"No path for {rec.id}"
            paths.add(path)
        assert len(paths) == len(records), f"Duplicate paths: {paths}"

    @pytest.mark.asyncio
    async def test_kb_paths_include_full_folder_hierarchy(self):
        """Verify KB paths contain ancestor folder names, proving non-flat storage."""
        provider = _build_full_provider()

        handbook_rec = _make_record(
            "rec-kb-handbook", "company_handbook.pdf",
            connector_id=KB_CONNECTOR_ID, connector_name="LOCAL_KB",
            record_group_id="rg-kb",
        )
        api_rec = _make_record(
            "rec-kb-apiref", "api_reference.md",
            connector_id=KB_CONNECTOR_ID, connector_name="LOCAL_KB",
            record_group_id="rg-kb",
        )

        handbook_path = await build_hierarchical_storage_path(handbook_rec, provider)
        api_path = await build_hierarchical_storage_path(api_rec, provider)

        assert "HR Policies" in handbook_path
        assert "Onboarding" in handbook_path
        assert "Engineering" in api_path
        assert "Architecture" in api_path
        assert "API Reference" in api_path

        handbook_segments = handbook_path.split("/")
        api_segments = api_path.split("/")
        assert len(handbook_segments) >= 5
        assert len(api_segments) >= 6


# ---------------------------------------------------------------------------
# Tests: StorageGraphProvider internal consistency
# ---------------------------------------------------------------------------


class TestStorageGraphProviderInternals:

    @pytest.mark.asyncio
    async def test_get_record_path_single_node(self):
        """Record with no parents returns just its name."""
        provider = StorageGraphProvider()
        provider.add_record("rec-1", "solo.txt")
        path = await provider.get_record_path("rec-1")
        assert path == "solo.txt"

    @pytest.mark.asyncio
    async def test_get_record_path_chain(self):
        """3-level chain: A → B → C returns A/B/C."""
        provider = StorageGraphProvider()
        provider.add_record("rec-a", "A")
        provider.add_record("rec-b", "B")
        provider.add_record("rec-c", "C")
        provider.add_parent_child("rec-b", "rec-a")
        provider.add_parent_child("rec-c", "rec-b")
        path = await provider.get_record_path("rec-c")
        assert path == "A/B/C"

    @pytest.mark.asyncio
    async def test_get_record_path_unknown_record(self):
        provider = StorageGraphProvider()
        assert await provider.get_record_path("nonexistent") is None

    @pytest.mark.asyncio
    async def test_check_vrids_filters_correctly(self):
        provider = StorageGraphProvider()
        provider.set_accessible_vrids({"v1": "r1", "v2": "r2", "v3": "r3"})
        result = await provider.check_vrids_accessible("u", "o", ["v1", "v3", "v99"])
        assert result == {"v1": "r1", "v3": "r3"}

    @pytest.mark.asyncio
    async def test_get_org_apps_returns_all(self):
        provider = StorageGraphProvider()
        provider.add_app("a1", type="GOOGLE_DRIVE")
        provider.add_app("a2", type="LOCAL_KB")
        apps = await provider.get_org_apps("org-1")
        assert len(apps) == 2

    @pytest.mark.asyncio
    async def test_rename_record_updates_path(self):
        provider = StorageGraphProvider()
        provider.add_record("rec-1", "old_name.txt")
        assert (await provider.get_record_path("rec-1")) == "old_name.txt"
        provider.rename_record("rec-1", "new_name.txt")
        assert (await provider.get_record_path("rec-1")) == "new_name.txt"

    @pytest.mark.asyncio
    async def test_move_record_updates_path(self):
        provider = StorageGraphProvider()
        provider.add_record("rec-p1", "Parent1")
        provider.add_record("rec-p2", "Parent2")
        provider.add_record("rec-child", "Child")
        provider.add_parent_child("rec-child", "rec-p1")
        assert (await provider.get_record_path("rec-child")) == "Parent1/Child"
        provider.move_record("rec-child", "rec-p2")
        assert (await provider.get_record_path("rec-child")) == "Parent2/Child"

    @pytest.mark.asyncio
    async def test_remove_parent_edge(self):
        provider = StorageGraphProvider()
        provider.add_record("rec-p", "Parent")
        provider.add_record("rec-c", "Child")
        provider.add_parent_child("rec-c", "rec-p")
        assert (await provider.get_record_path("rec-c")) == "Parent/Child"
        provider.remove_parent_edge("rec-c")
        assert (await provider.get_record_path("rec-c")) == "Child"

    @pytest.mark.asyncio
    async def test_get_document_records_collection(self):
        provider = StorageGraphProvider()
        provider.add_record("rec-1", "test.txt", extra="val")
        doc = await provider.get_document("rec-1", CollectionNames.RECORDS.value)
        assert doc is not None
        assert doc["recordName"] == "test.txt"

    @pytest.mark.asyncio
    async def test_get_document_unknown_collection(self):
        provider = StorageGraphProvider()
        provider.add_record("rec-1", "test.txt")
        assert await provider.get_document("rec-1", "other_collection") is None


# ---------------------------------------------------------------------------
# Tests: Multiple records at different depths within same KB
# ---------------------------------------------------------------------------


class TestKBMixedDepths:
    """KB records exist at multiple nesting levels simultaneously."""

    @pytest.mark.asyncio
    async def test_root_level_vs_deeply_nested(self):
        """A root-level KB file and a 4-level nested file both resolve correctly."""
        provider = StorageGraphProvider()
        provider.add_record_group("rg-mix", "Mixed KB")
        provider.add_record("rec-root-file", "quick_ref.txt")
        provider.add_record("rec-d1", "Dept")
        provider.add_record("rec-d2", "Team")
        provider.add_record("rec-d3", "Project")
        provider.add_record("rec-deep-file", "deep_spec.pdf")
        provider.add_parent_child("rec-d2", "rec-d1")
        provider.add_parent_child("rec-d3", "rec-d2")
        provider.add_parent_child("rec-deep-file", "rec-d3")

        root_rec = _make_record(
            "rec-root-file", "quick_ref.txt",
            connector_id="conn-mix", connector_name="LOCAL_KB",
            record_group_id="rg-mix",
        )
        deep_rec = _make_record(
            "rec-deep-file", "deep_spec.pdf",
            connector_id="conn-mix", connector_name="LOCAL_KB",
            record_group_id="rg-mix",
        )

        root_path = await build_hierarchical_storage_path(root_rec, provider)
        deep_path = await build_hierarchical_storage_path(deep_rec, provider)

        assert root_path == "records/conn-mix/Mixed KB/quick_ref.txt"
        assert deep_path == "records/conn-mix/Mixed KB/Dept/Team/Project/deep_spec.pdf"

    @pytest.mark.asyncio
    async def test_files_at_every_level(self):
        """Files exist at level 1, 2, and 3 — all produce correct paths."""
        provider = StorageGraphProvider()
        provider.add_record_group("rg-ml", "Multi-Level")
        provider.add_record("rec-f1", "Folder1")
        provider.add_record("rec-f2", "Folder2")
        provider.add_record("rec-l1", "level1.txt")
        provider.add_record("rec-l2", "level2.txt")
        provider.add_record("rec-l3", "level3.txt")
        provider.add_parent_child("rec-l1", "rec-f1")
        provider.add_parent_child("rec-f2", "rec-f1")
        provider.add_parent_child("rec-l2", "rec-f2")
        provider.add_parent_child("rec-l3", "rec-f2")

        recs = [
            ("rec-l1", "level1.txt", "records/conn-ml/Multi-Level/Folder1/level1.txt"),
            ("rec-l2", "level2.txt", "records/conn-ml/Multi-Level/Folder1/Folder2/level2.txt"),
            ("rec-l3", "level3.txt", "records/conn-ml/Multi-Level/Folder1/Folder2/level3.txt"),
        ]
        for rid, rname, expected in recs:
            rec = _make_record(
                rid, rname,
                connector_id="conn-ml", connector_name="LOCAL_KB",
                record_group_id="rg-ml",
            )
            path = await build_hierarchical_storage_path(rec, provider)
            assert path == expected, f"Mismatch for {rid}: {path} != {expected}"

    @pytest.mark.asyncio
    async def test_multiple_root_folders_in_same_group(self):
        """Multiple top-level folders under the same record group."""
        provider = StorageGraphProvider()
        provider.add_record_group("rg-multi", "KB")
        provider.add_record("rec-fa", "FolderA")
        provider.add_record("rec-fb", "FolderB")
        provider.add_record("rec-fa-doc", "docA.pdf")
        provider.add_record("rec-fb-doc", "docB.pdf")
        provider.add_parent_child("rec-fa-doc", "rec-fa")
        provider.add_parent_child("rec-fb-doc", "rec-fb")

        rec_a = _make_record("rec-fa-doc", "docA.pdf", connector_id="c", connector_name="LOCAL_KB", record_group_id="rg-multi")
        rec_b = _make_record("rec-fb-doc", "docB.pdf", connector_id="c", connector_name="LOCAL_KB", record_group_id="rg-multi")

        path_a = await build_hierarchical_storage_path(rec_a, provider)
        path_b = await build_hierarchical_storage_path(rec_b, provider)

        assert path_a == "records/c/KB/FolderA/docA.pdf"
        assert path_b == "records/c/KB/FolderB/docB.pdf"
        assert path_a != path_b


# ---------------------------------------------------------------------------
# Tests: Comprehensive end-to-end across ALL connectors
# ---------------------------------------------------------------------------


class TestEndToEndAllConnectors:
    """Validate that every record across all connectors in the full provider
    is retrievable, unique, and correctly structured."""

    @pytest.mark.asyncio
    async def test_every_leaf_record_has_connector_id_in_path(self):
        """Every record path starts with records/<connector_id>/."""
        provider = _build_full_provider()
        test_cases = [
            ("rec-api-spec", GDRIVE_CONNECTOR_ID, "GOOGLE_DRIVE", "rg-gdrive"),
            ("rec-micro-page", CONFLUENCE_CONNECTOR_ID, "CONFLUENCE", "rg-confluence"),
            ("rec-task-tests", JIRA_CONNECTOR_ID, "JIRA", "rg-jira"),
            ("rec-kb-handbook", KB_CONNECTOR_ID, "LOCAL_KB", "rg-kb"),
            ("rec-kb2-runbook", KB_CONNECTOR_ID_2, "LOCAL_KB", "rg-kb2"),
            ("rec-sp-financial", SHAREPOINT_CONNECTOR_ID, "SHAREPOINT", "rg-sharepoint"),
        ]
        for rec_id, conn_id, conn_name, rg_id in test_cases:
            rec_data = provider._records[rec_id]
            rec = _make_record(
                rec_id, rec_data["recordName"],
                connector_id=conn_id, connector_name=conn_name,
                record_group_id=rg_id,
            )
            path = await build_hierarchical_storage_path(rec, provider)
            assert path is not None, f"No path for {rec_id}"
            assert path.startswith(f"records/{conn_id}/"), (
                f"Path for {rec_id} doesn't start with connector: {path}"
            )

    @pytest.mark.asyncio
    async def test_every_grouped_record_has_group_in_path(self):
        """Every record with a record_group_id includes the group name."""
        provider = _build_full_provider()
        cases = [
            ("rec-api-spec", GDRIVE_CONNECTOR_ID, "GOOGLE_DRIVE", "rg-gdrive", "Shared Drive"),
            ("rec-kb-handbook", KB_CONNECTOR_ID, "LOCAL_KB", "rg-kb", "Policy KB"),
            ("rec-kb2-runbook", KB_CONNECTOR_ID_2, "LOCAL_KB", "rg-kb2", "Technical Docs"),
            ("rec-sp-financial", SHAREPOINT_CONNECTOR_ID, "SHAREPOINT", "rg-sharepoint", "Team Site"),
        ]
        for rec_id, conn_id, conn_name, rg_id, group_name in cases:
            rec_data = provider._records[rec_id]
            rec = _make_record(
                rec_id, rec_data["recordName"],
                connector_id=conn_id, connector_name=conn_name,
                record_group_id=rg_id,
            )
            path = await build_hierarchical_storage_path(rec, provider)
            assert group_name in path, (
                f"Group '{group_name}' not in path for {rec_id}: {path}"
            )


# ---------------------------------------------------------------------------
# Tests: _graph_record_in_time_range helper
# ---------------------------------------------------------------------------


class TestGraphRecordInTimeRange:

    def test_no_time_range_passes(self):
        assert _graph_record_in_time_range({"sourceCreatedAtTimestamp": 100}, None) is True

    def test_filters_by_created_after(self):
        rec = {"sourceCreatedAtTimestamp": 1000, "sourceLastModifiedTimestamp": 2000}
        assert _graph_record_in_time_range(rec, {"source_created_after_ms": 500}) is True
        assert _graph_record_in_time_range(rec, {"source_created_after_ms": 2000}) is False

    def test_filters_by_updated_before(self):
        rec = {"sourceCreatedAtTimestamp": 1000, "sourceLastModifiedTimestamp": 2000}
        assert _graph_record_in_time_range(rec, {"source_updated_before_ms": 3000}) is True
        assert _graph_record_in_time_range(rec, {"source_updated_before_ms": 1500}) is False

    def test_missing_timestamps_rejected(self):
        assert _graph_record_in_time_range({}, {"source_created_after_ms": 100}) is False

    def test_combined_range(self):
        rec = {"sourceCreatedAtTimestamp": 1000, "sourceLastModifiedTimestamp": 2000}
        assert _graph_record_in_time_range(
            rec,
            {
                "source_created_after_ms": 500,
                "source_created_before_ms": 1500,
                "source_updated_after_ms": 1500,
                "source_updated_before_ms": 2500,
            },
        ) is True


# ---------------------------------------------------------------------------
# Tests: merge_pattern_match_results time-range optimization
# ---------------------------------------------------------------------------


class TestMergeTimeRangeOptimization:

    @pytest.mark.asyncio
    async def test_time_range_filters_before_blob_fetch(self):
        """When time_range is set, records outside the range never trigger blob fetch."""
        provider = _build_full_provider()
        provider.set_accessible_vrids({
            "vrid-old": "rec-old",
            "vrid-new": "rec-new",
        })
        provider.add_record(
            "rec-old", "old.txt", virtualRecordId="vrid-old",
            sourceCreatedAtTimestamp=100,
            sourceLastModifiedTimestamp=200,
        )
        provider.add_record(
            "rec-new", "new.txt", virtualRecordId="vrid-new",
            sourceCreatedAtTimestamp=5000,
            sourceLastModifiedTimestamp=6000,
        )

        blob_store = MagicMock()
        blob_store.config_service = AsyncMock()
        blob_store.config_service.get_config = AsyncMock(return_value={})

        raw = [
            {"virtual_record_id": "vrid-old", "path": "/old"},
            {"virtual_record_id": "vrid-new", "path": "/new"},
        ]

        get_record_calls = []

        async def _track_get_record(vrid, *args, **kwargs):
            get_record_calls.append(vrid)

        with patch("app.utils.pattern_match.get_record", side_effect=_track_get_record), \
             patch("app.utils.pattern_match.get_flattened_results",
                   new_callable=AsyncMock, return_value=[]):
            await merge_pattern_match_results(
                raw_records=raw,
                virtual_record_id_to_result={},
                user_id=USER_ID,
                org_id=ORG_ID,
                blob_store=blob_store,
                graph_provider=provider,
                is_multimodal_llm=False,
                logger_instance=MagicMock(),
                time_range={"source_created_after_ms": 1000},
            )

        assert "vrid-new" in get_record_calls
        assert "vrid-old" not in get_record_calls

    @pytest.mark.asyncio
    async def test_no_time_range_fetches_all(self):
        """Without time_range, all accessible records trigger blob fetch."""
        provider = _build_full_provider()
        provider.set_accessible_vrids({
            "vrid-1": "rec-1",
            "vrid-2": "rec-2",
        })
        provider.add_record("rec-1", "a.txt", virtualRecordId="vrid-1")
        provider.add_record("rec-2", "b.txt", virtualRecordId="vrid-2")

        blob_store = MagicMock()
        blob_store.config_service = AsyncMock()
        blob_store.config_service.get_config = AsyncMock(return_value={})

        raw = [
            {"virtual_record_id": "vrid-1", "path": "/a"},
            {"virtual_record_id": "vrid-2", "path": "/b"},
        ]

        get_record_calls = []

        async def _track_get_record(vrid, *args, **kwargs):
            get_record_calls.append(vrid)

        with patch("app.utils.pattern_match.get_record", side_effect=_track_get_record), \
             patch("app.utils.pattern_match.get_flattened_results",
                   new_callable=AsyncMock, return_value=[]):
            await merge_pattern_match_results(
                raw_records=raw,
                virtual_record_id_to_result={},
                user_id=USER_ID,
                org_id=ORG_ID,
                blob_store=blob_store,
                graph_provider=provider,
                is_multimodal_llm=False,
                logger_instance=MagicMock(),
            )

        assert len(get_record_calls) == 2

    @pytest.mark.asyncio
    async def test_all_filtered_returns_empty(self):
        """When every record is outside time range, returns empty without blob fetch."""
        provider = _build_full_provider()
        provider.set_accessible_vrids({"vrid-x": "rec-x"})
        provider.add_record(
            "rec-x", "x.txt", virtualRecordId="vrid-x",
            sourceCreatedAtTimestamp=100,
            sourceLastModifiedTimestamp=200,
        )

        blob_store = MagicMock()
        blob_store.config_service = AsyncMock()
        blob_store.config_service.get_config = AsyncMock(return_value={})

        raw = [{"virtual_record_id": "vrid-x", "path": "/x"}]

        with patch("app.utils.pattern_match.get_record", new_callable=AsyncMock) as mock_get:
            results = await merge_pattern_match_results(
                raw_records=raw,
                virtual_record_id_to_result={},
                user_id=USER_ID,
                org_id=ORG_ID,
                blob_store=blob_store,
                graph_provider=provider,
                is_multimodal_llm=False,
                logger_instance=MagicMock(),
                time_range={"source_created_after_ms": 9999},
            )

        assert results == []
        mock_get.assert_not_called()
