"""`ArtifactManager.get_record_download_url` must authorize the caller per user,
not just per org, before minting a signed URL or describing a connector record.

Runs the real `TieredRecordAuthorizer` over a fake graph so each tier
(org scope, direct permission edge, full ACL) is exercised as in production.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.agents.actions.artifacts.artifacts import ArtifactManager
from app.config.constants.arangodb import OriginTypes

_ORG = "org-1"
_OWNER = "owner-1"
_OTHER = "other-1"
_USER_KEYS = {_OWNER: "ukey-owner", _OTHER: "ukey-other"}


def _record(
    record_id: str = "rec-1",
    *,
    org_id: str = _ORG,
    origin: OriginTypes = OriginTypes.UPLOAD,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=record_id,
        org_id=org_id,
        origin=origin,
        external_record_id=f"blob-{record_id}",
        record_name="salary-review.pdf",
        mime_type="application/pdf",
        weburl="https://drive.example/salary-review",
        virtual_record_id=f"vr-{record_id}",
    )


def _graph(
    records: dict[str, SimpleNamespace],
    *,
    edges: set[tuple[str, str]] = frozenset(),
    tier3: set[tuple[str, str]] = frozenset(),
) -> MagicMock:
    graph = MagicMock()
    graph.get_record_by_id = AsyncMock(side_effect=lambda rid, *a, **k: records.get(rid))
    graph.get_user_by_user_id = AsyncMock(
        side_effect=lambda uid: {"_key": _USER_KEYS[uid]} if uid in _USER_KEYS else None,
    )
    graph.get_edge = AsyncMock(
        side_effect=lambda *, from_id, to_id, **_: {"_key": "e"} if (from_id, to_id) in edges else None,
    )
    graph.check_record_access_with_details = AsyncMock(
        side_effect=lambda uid, org, rid: {"record": {}} if (uid, rid) in tier3 else None,
    )
    return graph


def _manager(graph: MagicMock, *, user_id: str = _OWNER) -> tuple[ArtifactManager, MagicMock]:
    blob_store = MagicMock()
    blob_store.get_download_url = AsyncMock(return_value="https://blob.example/signed")
    manager = ArtifactManager({
        "org_id": _ORG, "user_id": user_id, "conversation_id": "conv-1",
        "graph_provider": graph, "blob_store": blob_store,
    })
    return manager, blob_store


async def _call(manager: ArtifactManager, record_id: str = "rec-1") -> tuple[bool, dict]:
    success, payload = await manager.get_record_download_url(record_id)
    return success, json.loads(payload)


async def _not_found_body(record_id: str = "rec-1") -> dict:
    manager, _ = _manager(_graph({}))
    _, body = await _call(manager, record_id)
    return body


class TestUploadedRecords:
    async def test_owner_gets_signed_url(self) -> None:  # T1
        graph = _graph({"rec-1": _record()}, edges={("ukey-owner", "rec-1")})
        manager, blob_store = _manager(graph)

        success, body = await _call(manager)

        assert success is True
        assert body["download_url"] == "https://blob.example/signed"
        assert body["file_name"] == "salary-review.pdf"
        blob_store.get_download_url.assert_awaited_once_with(_ORG, "blob-rec-1")
        graph.check_record_access_with_details.assert_not_awaited()

    async def test_same_org_user_without_permission_is_denied_before_minting(self) -> None:  # T2
        graph = _graph({"rec-1": _record()}, edges={("ukey-owner", "rec-1")})
        manager, blob_store = _manager(graph, user_id=_OTHER)

        success, body = await _call(manager)

        assert success is False
        assert "download_url" not in body
        assert "file_name" not in body
        assert "salary-review" not in json.dumps(body)
        blob_store.get_download_url.assert_not_called()

    async def test_access_through_full_acl_tier_gets_url(self) -> None:  # T3
        graph = _graph({"rec-1": _record()}, tier3={(_OTHER, "rec-1")})
        manager, blob_store = _manager(graph, user_id=_OTHER)

        success, body = await _call(manager)

        assert success is True
        assert body["download_url"] == "https://blob.example/signed"
        blob_store.get_download_url.assert_awaited_once()

    async def test_cross_org_record_is_denied_at_org_tier(self) -> None:  # T6
        graph = _graph({"rec-1": _record(org_id="org-2")}, edges={("ukey-owner", "rec-1")})
        manager, blob_store = _manager(graph)

        success, body = await _call(manager)

        assert success is False
        assert body == await _not_found_body()
        graph.get_edge.assert_not_awaited()
        blob_store.get_download_url.assert_not_called()

    async def test_authorizer_io_error_fails_closed(self) -> None:  # T7
        graph = _graph({"rec-1": _record()})
        graph.get_edge = AsyncMock(side_effect=ConnectionError("graph down"))
        manager, blob_store = _manager(graph)

        success, body = await _call(manager)

        assert success is False
        assert "download_url" not in body
        assert "file_name" not in body
        blob_store.get_download_url.assert_not_called()

    async def test_user_node_missing_falls_through_to_full_acl_and_denies(self) -> None:
        graph = _graph({"rec-1": _record()})
        manager, blob_store = _manager(graph, user_id="no-graph-node")

        success, body = await _call(manager)

        assert success is False
        assert body == await _not_found_body()
        graph.check_record_access_with_details.assert_awaited_once()
        blob_store.get_download_url.assert_not_called()


class TestConnectorRecords:
    async def test_denied_connector_record_leaks_nothing(self) -> None:  # T4
        graph = _graph({"rec-1": _record(origin=OriginTypes.CONNECTOR)})
        manager, blob_store = _manager(graph, user_id=_OTHER)

        success, body = await _call(manager)

        assert success is False
        assert "source_url" not in body
        assert "file_name" not in body
        assert body == await _not_found_body()
        blob_store.get_download_url.assert_not_called()

    async def test_readable_connector_record_returns_source_link(self) -> None:  # T5
        graph = _graph(
            {"rec-1": _record(origin=OriginTypes.CONNECTOR)}, tier3={(_OTHER, "rec-1")},
        )
        manager, blob_store = _manager(graph, user_id=_OTHER)

        success, body = await _call(manager)

        assert success is False
        assert body["source_url"] == "https://drive.example/salary-review"
        assert body["file_name"] == "salary-review.pdf"
        blob_store.get_download_url.assert_not_called()


class TestNoExistenceOracle:
    async def test_missing_record_and_denied_record_are_indistinguishable(self) -> None:  # T8
        denied_manager, _ = _manager(_graph({"rec-1": _record()}), user_id=_OTHER)
        _, denied = await _call(denied_manager)

        assert denied == await _not_found_body()

    async def test_graph_lookup_failure_returns_no_url(self) -> None:
        graph = _graph({})
        graph.get_record_by_id = AsyncMock(side_effect=ConnectionError("graph down"))
        manager, blob_store = _manager(graph)

        success, body = await _call(manager)

        assert success is False
        assert "download_url" not in body
        blob_store.get_download_url.assert_not_called()
