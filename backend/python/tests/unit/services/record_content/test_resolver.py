"""Unit tests for RecordContentResolver.

Covers:
- Happy path: graph lookup → authorize → blob strategy → ResolvedRecordContent
- Graph lookup returns None + artifact fallback succeeds
- Graph lookup returns None + no artifact registry → RecordNotFoundError
- Authorizer denies → RecordAccessDeniedError (no fetch called)
- Unsupported record origin → RecordContentError
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.org_context import MissingOrgContext, OrgMismatch, SystemContextNotAllowed
from app.services.record_content.models import (
    RecordAccessDeniedError,
    RecordContentError,
    RecordContentUnavailableError,
    RecordNotFoundError,
    ResolvedRecordContent,
)
from app.services.record_content.resolver import RecordContentResolver
from app.utils.error_class import classify_error, mark_error
from tests.support.record_access import InMemoryRecordAccess


def _make_actor(org_id="org1", user_id="user1"):
    actor = MagicMock()
    actor.org_id = org_id
    actor.user_id = user_id
    return actor


def _make_record(record_id="rec1", org_id="org1", origin="UPLOAD", filename="test.txt", ext_id=None):
    r = MagicMock()
    r.id = record_id
    r.org_id = org_id
    r.origin = origin
    r.externalRecordId = ext_id
    r.get = lambda key, default=None: {
        "orgId": org_id,
        "_key": record_id,
        "origin": origin,
        "externalRecordId": ext_id,
        "name": filename,
    }.get(key, default)
    return r


def _make_resolved(record_id="rec1"):
    return ResolvedRecordContent(
        record_id=record_id,
        filename="test.txt",
        mime_type="text/plain",
        size_bytes=4,
        content=b"data",
        version=None,
        source="blob",
    )


@pytest.fixture
def graph():
    g = AsyncMock()
    g.get_record_by_id = AsyncMock(return_value=None)
    InMemoryRecordAccess().readable("user1", "org1", "rec1").serve(g)
    g.get_edge = AsyncMock(return_value={"_id": "e"})
    return g


@pytest.fixture
def config():
    return MagicMock()


@pytest.mark.asyncio
async def test_happy_path_blob(graph, config):
    """Records with UPLOAD origin hit BlobBackedContentStrategy."""
    record = _make_record(origin="UPLOAD")
    graph.get_record_by_id.return_value = record
    resolved = _make_resolved()

    resolver = RecordContentResolver(graph_provider=graph, config_service=config)

    with (
        patch.object(resolver._authorizer, "authorize", new=AsyncMock()),
        patch.object(resolver._blob_strategy, "supports", return_value=True),
        patch.object(resolver._blob_strategy, "fetch", new=AsyncMock(return_value=b"data")),
        patch(
            "app.services.record_content.resolver.build_resolved_content",
            return_value=resolved,
        ),
    ):
        result = await resolver.resolve(
            actor=_make_actor(), ref="rec1", max_bytes=1000
        )

    assert result.record_id == "rec1"
    assert result.content == b"data"


@pytest.mark.asyncio
async def test_not_found_raises(graph, config):
    """Graph returns None and no artifact registry → RecordNotFoundError."""
    graph.get_record_by_id.return_value = None
    resolver = RecordContentResolver(graph_provider=graph, config_service=config)

    with pytest.raises(RecordNotFoundError):
        await resolver.resolve(actor=_make_actor(), ref="nonexistent", max_bytes=1000)


@pytest.mark.asyncio
async def test_authorizer_deny_no_fetch(graph, config):
    """RecordAccessDeniedError from authorizer → fetch strategy never called."""
    record = _make_record(origin="UPLOAD")
    graph.get_record_by_id.return_value = record

    resolver = RecordContentResolver(graph_provider=graph, config_service=config)

    with (
        patch.object(
            resolver._authorizer, "authorize",
            new=AsyncMock(side_effect=RecordAccessDeniedError("denied")),
        ),
        patch.object(resolver._blob_strategy, "fetch", new=AsyncMock()) as mock_fetch,
    ):
        with pytest.raises(RecordAccessDeniedError):
            await resolver.resolve(actor=_make_actor(), ref="rec1", max_bytes=1000)

        mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_unsupported_origin_raises(graph, config):
    """No strategy supports the record → RecordContentError."""
    record = _make_record(origin="UNKNOWN")
    graph.get_record_by_id.return_value = record

    resolver = RecordContentResolver(graph_provider=graph, config_service=config)

    with (
        patch.object(resolver._authorizer, "authorize", new=AsyncMock()),
        patch.object(resolver._blob_strategy, "supports", return_value=False),
        patch.object(resolver._connector_strategy, "supports", return_value=False),
    ):
        with pytest.raises(RecordContentError):
            await resolver.resolve(actor=_make_actor(), ref="rec1", max_bytes=1000)


@pytest.mark.asyncio
async def test_artifact_fallback_used_when_graph_none(graph, config):
    """When graph returns None and artifact_registry is set with conversation_id,
    the artifact registry is consulted."""
    graph.get_record_by_id.side_effect = [None, _make_record(origin="UPLOAD")]
    resolved = _make_resolved()

    mock_registry = MagicMock()
    mock_metadata = MagicMock()
    mock_metadata.artifact_id = "rec1"
    mock_registry.resolve = AsyncMock(return_value=mock_metadata)

    resolver = RecordContentResolver(
        graph_provider=graph, config_service=config, artifact_registry=mock_registry
    )

    with (
        patch.object(resolver._authorizer, "authorize", new=AsyncMock()),
        patch.object(resolver._blob_strategy, "supports", return_value=True),
        patch.object(resolver._blob_strategy, "fetch", new=AsyncMock(return_value=b"data")),
        patch(
            "app.services.record_content.resolver.build_resolved_content",
            return_value=resolved,
        ),
    ):
        result = await resolver.resolve(
            actor=_make_actor(), ref="report.pdf",
            conversation_id="conv1", max_bytes=1000
        )

    mock_registry.resolve.assert_called_once()
    assert result.record_id == "rec1"


class TestArtifactsAreReadAsTheirOwner:
    """A service-account agent reads knowledge as its creator, while the
    artifacts of the chat belong to the person chatting."""

    @staticmethod
    def _record(record_type: object) -> MagicMock:
        record = _make_record(origin="UPLOAD")
        record.record_type = record_type
        return record

    async def _resolve(self, graph: AsyncMock, config: MagicMock, record: MagicMock, **kwargs: object) -> tuple:
        graph.get_record_by_id.return_value = record
        resolver = RecordContentResolver(graph_provider=graph, config_service=config)
        authorize = AsyncMock()
        fetch = AsyncMock(return_value=b"data")
        with (
            patch.object(resolver._authorizer, "authorize", new=authorize),
            patch.object(resolver._blob_strategy, "supports", return_value=True),
            patch.object(resolver._blob_strategy, "fetch", new=fetch),
            patch("app.services.record_content.resolver.build_resolved_content", return_value=_make_resolved()),
        ):
            await resolver.resolve(ref="rec1", max_bytes=1000, **kwargs)
        return authorize.await_args.args[0], fetch.await_args.kwargs["actor"]

    @pytest.mark.asyncio
    async def test_an_artifact_is_checked_and_fetched_as_its_owner(self, graph: AsyncMock, config: MagicMock) -> None:
        from app.models.entities import RecordType

        creator, caller = _make_actor(user_id="u-alice"), _make_actor(user_id="u-bob")

        checked, fetched = await self._resolve(
            graph, config, self._record(RecordType.ARTIFACT), actor=creator, artifact_actor=caller,
        )

        assert checked is caller and fetched is caller

    @pytest.mark.asyncio
    async def test_a_knowledge_record_is_still_read_as_the_run_user(self, graph: AsyncMock, config: MagicMock) -> None:
        from app.models.entities import RecordType

        creator, caller = _make_actor(user_id="u-alice"), _make_actor(user_id="u-bob")

        checked, fetched = await self._resolve(
            graph, config, self._record(RecordType.FILE), actor=creator, artifact_actor=caller,
        )

        assert checked is creator and fetched is creator

    @pytest.mark.asyncio
    async def test_without_an_artifact_owner_everything_is_read_as_the_run_user(self, graph: AsyncMock, config: MagicMock) -> None:
        from app.models.entities import RecordType

        creator = _make_actor(user_id="u-alice")

        checked, _ = await self._resolve(graph, config, self._record(RecordType.ARTIFACT), actor=creator)

        assert checked is creator

    @pytest.mark.asyncio
    async def test_an_artifact_named_in_the_chat_is_looked_up_as_its_owner(self, graph: AsyncMock, config: MagicMock) -> None:
        from app.models.entities import RecordType

        record = self._record(RecordType.ARTIFACT)
        graph.get_record_by_id = AsyncMock(side_effect=[None, record])
        artifacts = MagicMock()
        artifacts.resolve = AsyncMock(return_value=MagicMock(artifact_id="rec1"))
        resolver = RecordContentResolver(graph_provider=graph, config_service=config, artifact_registry=artifacts)
        creator, caller = _make_actor(user_id="u-alice"), _make_actor(user_id="u-bob")

        with (
            patch.object(resolver._authorizer, "authorize", new=AsyncMock()),
            patch.object(resolver._blob_strategy, "supports", return_value=True),
            patch.object(resolver._blob_strategy, "fetch", new=AsyncMock(return_value=b"data")),
            patch("app.services.record_content.resolver.build_resolved_content", return_value=_make_resolved()),
        ):
            await resolver.resolve(
                actor=creator, artifact_actor=caller, ref="summary.pdf", conversation_id="conv-1", max_bytes=1000,
            )

        assert artifacts.resolve.await_args.kwargs["actor"] is caller


class TestGraphOutage:
    """A graph that cannot be read is not a missing record (F58): the agent was told
    "Record not found" for a file that exists, and gave up on it."""

    async def test_a_failed_lookup_is_unavailable_not_not_found(self, graph: AsyncMock, config: MagicMock) -> None:
        down = RuntimeError("Neo4j driver not connected")  # what the Neo4j client raises, stamped as the db layer does
        mark_error(down, "unavailable")
        graph.get_record_by_id = AsyncMock(side_effect=down)
        resolver = RecordContentResolver(graph_provider=graph, config_service=config)
        with pytest.raises(RecordContentUnavailableError) as raised:
            await resolver.resolve(actor=_make_actor(), ref="rec1", max_bytes=1000)
        assert not isinstance(raised.value, RecordNotFoundError)
        assert raised.value.__cause__ is down
        assert classify_error(raised.value) == "unavailable"

    async def test_the_lookup_asks_the_provider_to_raise(self, graph: AsyncMock, config: MagicMock) -> None:
        resolver = RecordContentResolver(graph_provider=graph, config_service=config)
        with pytest.raises(RecordNotFoundError):
            await resolver.resolve(actor=_make_actor(), ref="rec1", max_bytes=1000)
        graph.get_record_by_id.assert_awaited_once_with("rec1", raise_on_error=True)

    async def test_a_failed_lookup_does_not_fall_back_to_the_artifact_registry(self, graph: AsyncMock, config: MagicMock) -> None:
        graph.get_record_by_id = AsyncMock(side_effect=ConnectionError("graph down"))
        registry = MagicMock()
        registry.resolve = AsyncMock()
        resolver = RecordContentResolver(graph_provider=graph, config_service=config, artifact_registry=registry)
        with pytest.raises(RecordContentUnavailableError):
            await resolver.resolve(actor=_make_actor(), ref="rec1", conversation_id="conv1", max_bytes=1000)
        registry.resolve.assert_not_awaited()

    async def test_a_failed_artifact_refetch_is_unavailable(self, graph: AsyncMock, config: MagicMock) -> None:
        graph.get_record_by_id = AsyncMock(side_effect=[None, ConnectionError("graph down")])
        registry = MagicMock()
        registry.resolve = AsyncMock(return_value=MagicMock(artifact_id="art1"))
        resolver = RecordContentResolver(graph_provider=graph, config_service=config, artifact_registry=registry)
        with pytest.raises(RecordContentUnavailableError):
            await resolver.resolve(actor=_make_actor(), ref="report.pdf", conversation_id="conv1", max_bytes=1000)
        assert graph.get_record_by_id.await_args_list[1].kwargs == {"raise_on_error": True}

    async def test_the_resolver_leaves_logging_the_failure_to_its_caller(
        self, graph: AsyncMock, config: MagicMock, caplog: pytest.LogCaptureFixture,
    ) -> None:
        graph.get_record_by_id = AsyncMock(side_effect=ConnectionError("graph down"))
        resolver = RecordContentResolver(graph_provider=graph, config_service=config)
        with caplog.at_level(logging.DEBUG, logger="app.services.record_content"), pytest.raises(RecordContentUnavailableError):
            await resolver.resolve(actor=_make_actor(), ref="rec1", max_bytes=1000)
        assert not [r for r in caplog.records if r.name.startswith("app.services.record_content")]

    async def test_the_message_names_no_backend_detail(self, graph: AsyncMock, config: MagicMock) -> None:
        graph.get_record_by_id = AsyncMock(side_effect=ConnectionError("bolt://10.0.0.7:7687 refused"))
        resolver = RecordContentResolver(graph_provider=graph, config_service=config)
        with pytest.raises(RecordContentUnavailableError) as raised:
            await resolver.resolve(actor=_make_actor(), ref="rec1", max_bytes=1000)
        assert "10.0.0.7" not in str(raised.value)
        assert "not found" not in str(raised.value).lower()

    @pytest.mark.parametrize("bug", [MissingOrgContext, SystemContextNotAllowed, OrgMismatch])
    async def test_an_org_context_bug_is_not_reported_as_an_outage(
        self, graph: AsyncMock, config: MagicMock, bug: type[Exception],
    ) -> None:
        """No query ran, so nothing else logged it: it must reach the caller as itself."""
        graph.get_record_by_id = AsyncMock(side_effect=bug("no org context is bound"))
        resolver = RecordContentResolver(graph_provider=graph, config_service=config)
        with pytest.raises(bug):
            await resolver.resolve(actor=_make_actor(), ref="rec1", max_bytes=1000)
