"""Unit tests for `PrerequisiteValidator` -- Part A2/E of the plan. Checked
against a `FakeGraphProvider` double that only implements the two methods
the validator actually calls (`get_user_connector_instances`,
`get_user_kb_permission`), since the real `IGraphDBProvider` contract is
already proven elsewhere (`test_task_store_contract.py`)."""
from __future__ import annotations

from app.services.tasks.application.prerequisites import (
    PrerequisiteCheckResult,
    PrerequisiteIssue,
    PrerequisiteValidator,
)


class FakeConnectorGraphProvider:
    """Stands in for `IGraphDBProvider` for the two calls
    `PrerequisiteValidator` makes: `get_user_connector_instances` and
    `get_user_kb_permission`."""

    def __init__(
        self,
        *,
        connector_instances: list[dict] | None = None,
        kb_permissions: dict[str, str | None] | None = None,
        connector_error: Exception | None = None,
        kb_error: Exception | None = None,
    ) -> None:
        self._connector_instances = connector_instances or []
        self._kb_permissions = kb_permissions or {}
        self._connector_error = connector_error
        self._kb_error = kb_error
        self.connector_calls: list[dict] = []
        self.kb_calls: list[tuple[str, str]] = []

    async def get_user_connector_instances(self, **kwargs: object) -> list[dict]:
        self.connector_calls.append(dict(kwargs))
        if self._connector_error is not None:
            raise self._connector_error
        return self._connector_instances

    async def get_user_kb_permission(self, kb_id: str, user_id: str) -> str | None:
        self.kb_calls.append((kb_id, user_id))
        if self._kb_error is not None:
            raise self._kb_error
        return self._kb_permissions.get(kb_id)


class TestPrerequisiteCheckResult:
    def test_ok_when_no_issues(self) -> None:
        assert PrerequisiteCheckResult().ok is True

    def test_ok_false_with_a_blocking_issue(self) -> None:
        result = PrerequisiteCheckResult(issues=[PrerequisiteIssue(kind="connector", id="c1", reason="missing")])
        assert result.ok is False
        assert result.blocking_issues == result.issues

    def test_ok_true_with_only_non_blocking_issues(self) -> None:
        result = PrerequisiteCheckResult(
            issues=[PrerequisiteIssue(kind="mcp_server", id="m1", reason="unverifiable", blocking=False)],
        )
        assert result.ok is True
        assert result.blocking_issues == []

    def test_summary_lists_every_issue(self) -> None:
        result = PrerequisiteCheckResult(issues=[
            PrerequisiteIssue(kind="connector", id="c1", reason="not connected"),
            PrerequisiteIssue(kind="collection", id="kb1", reason="not found"),
        ])
        summary = result.summary()
        assert "connector 'c1': not connected" in summary
        assert "collection 'kb1': not found" in summary

    def test_summary_of_no_issues(self) -> None:
        assert PrerequisiteCheckResult().summary() == "All prerequisites satisfied."


class TestValidateConnectors:
    async def test_no_connector_ids_is_a_noop(self) -> None:
        provider = FakeConnectorGraphProvider()
        validator = PrerequisiteValidator()
        result = await validator.validate(
            org_id="org-1", user_id="user-1", connector_ids=[], collection_ids=[],
            mcp_server_ids=[], graph_provider=provider,
        )
        assert result.ok is True
        assert provider.connector_calls == []

    async def test_configured_and_authenticated_connector_passes(self) -> None:
        provider = FakeConnectorGraphProvider(connector_instances=[
            {"_key": "conn-1", "isConfigured": True, "isAuthenticated": True},
        ])
        validator = PrerequisiteValidator()
        result = await validator.validate(
            org_id="org-1", user_id="user-1", connector_ids=["conn-1"], collection_ids=[],
            mcp_server_ids=[], graph_provider=provider,
        )
        assert result.ok is True
        assert result.issues == []

    async def test_missing_connector_is_blocking(self) -> None:
        provider = FakeConnectorGraphProvider(connector_instances=[])
        validator = PrerequisiteValidator()
        result = await validator.validate(
            org_id="org-1", user_id="user-1", connector_ids=["conn-missing"], collection_ids=[],
            mcp_server_ids=[], graph_provider=provider,
        )
        assert result.ok is False
        assert result.issues[0].kind == "connector"
        assert result.issues[0].id == "conn-missing"
        assert result.issues[0].blocking is True

    async def test_unconfigured_connector_is_blocking(self) -> None:
        provider = FakeConnectorGraphProvider(connector_instances=[
            {"_key": "conn-1", "isConfigured": False},
        ])
        validator = PrerequisiteValidator()
        result = await validator.validate(
            org_id="org-1", user_id="user-1", connector_ids=["conn-1"], collection_ids=[],
            mcp_server_ids=[], graph_provider=provider,
        )
        assert result.ok is False
        assert "not yet configured" in result.issues[0].reason

    async def test_unauthenticated_connector_is_blocking(self) -> None:
        provider = FakeConnectorGraphProvider(connector_instances=[
            {"_key": "conn-1", "isConfigured": True, "isAuthenticated": False},
        ])
        validator = PrerequisiteValidator()
        result = await validator.validate(
            org_id="org-1", user_id="user-1", connector_ids=["conn-1"], collection_ids=[],
            mcp_server_ids=[], graph_provider=provider,
        )
        assert result.ok is False
        assert "reconnect required" in result.issues[0].reason

    async def test_connector_lookup_failure_is_reported_per_connector_and_never_raises(self) -> None:
        provider = FakeConnectorGraphProvider(connector_error=RuntimeError("graph db down"))
        validator = PrerequisiteValidator()
        result = await validator.validate(
            org_id="org-1", user_id="user-1", connector_ids=["c1", "c2"], collection_ids=[],
            mcp_server_ids=[], graph_provider=provider,
        )
        assert result.ok is False
        assert {i.id for i in result.issues} == {"c1", "c2"}
        assert all("could not verify" in i.reason for i in result.issues)


class TestValidateCollections:
    async def test_accessible_collection_passes(self) -> None:
        provider = FakeConnectorGraphProvider(kb_permissions={"kb-1": "reader"})
        validator = PrerequisiteValidator()
        result = await validator.validate(
            org_id="org-1", user_id="user-1", connector_ids=[], collection_ids=["kb-1"],
            mcp_server_ids=[], graph_provider=provider,
        )
        assert result.ok is True

    async def test_inaccessible_collection_is_blocking(self) -> None:
        provider = FakeConnectorGraphProvider(kb_permissions={})
        validator = PrerequisiteValidator()
        result = await validator.validate(
            org_id="org-1", user_id="user-1", connector_ids=[], collection_ids=["kb-missing"],
            mcp_server_ids=[], graph_provider=provider,
        )
        assert result.ok is False
        assert result.issues[0].kind == "collection"

    async def test_collection_lookup_failure_is_blocking_and_never_raises(self) -> None:
        provider = FakeConnectorGraphProvider(kb_error=RuntimeError("timeout"))
        validator = PrerequisiteValidator()
        result = await validator.validate(
            org_id="org-1", user_id="user-1", connector_ids=[], collection_ids=["kb-1"],
            mcp_server_ids=[], graph_provider=provider,
        )
        assert result.ok is False
        assert "could not verify access" in result.issues[0].reason


class TestValidateMcpServers:
    async def test_mcp_server_ids_are_reported_but_never_block(self) -> None:
        provider = FakeConnectorGraphProvider()
        validator = PrerequisiteValidator()
        result = await validator.validate(
            org_id="org-1", user_id="user-1", connector_ids=[], collection_ids=[],
            mcp_server_ids=["mcp-1"], graph_provider=provider,
        )
        assert result.ok is True
        assert len(result.issues) == 1
        assert result.issues[0].kind == "mcp_server"
        assert result.issues[0].blocking is False


class TestValidateTask:
    async def test_validate_task_reads_ids_from_principal_and_task(self) -> None:
        from app.services.tasks.domain.models import TaskDefinition, TaskPrincipal

        task = TaskDefinition(
            org_id="org-1", created_by_user_id="user-1",
            principal=TaskPrincipal(org_id="org-1", user_id="user-1", user_email="a@b.com"),
            title="t", description="d", instructions="i",
            connector_ids=["conn-1"], collection_ids=["kb-1"],
        )
        provider = FakeConnectorGraphProvider(
            connector_instances=[{"_key": "conn-1", "isConfigured": True, "isAuthenticated": True}],
            kb_permissions={"kb-1": "reader"},
        )
        validator = PrerequisiteValidator()
        result = await validator.validate_task(task, graph_provider=provider)
        assert result.ok is True
