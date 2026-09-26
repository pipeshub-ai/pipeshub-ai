"""Unit tests for app.utils.attachment_access — the per-user gate every chat
attachment id from a request payload (current turn and history) passes through
before any blob read."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.agents.agent_loop.cancellation.registry import RunOwner
from app.services.artifact_registry.models import Actor
from app.utils.attachment_access import (
    actors_for_run,
    authorize_attachments,
    authorize_query_attachments,
)

_ORG = "org-1"
_ALICE = "alice"
_BOB = "bob"
_USER_KEYS = {_ALICE: "ukey-alice", _BOB: "ukey-bob"}
_LOG = logging.getLogger("test_attachment_access")


def _record(record_id: str, *, org_id: str = _ORG, vrid: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=record_id, org_id=org_id, virtual_record_id=vrid or f"vr-{record_id}")


def _att(record_id: str | None, vrid: str | None = None, name: str = "secret-plan.pdf") -> dict:
    att = {"recordName": name, "mimeType": "application/pdf"}
    if record_id:
        att["recordId"] = record_id
        att["virtualRecordId"] = vrid or f"vr-{record_id}"
    elif vrid:
        att["virtualRecordId"] = vrid
    return att


def _graph(
    records: list[SimpleNamespace],
    *,
    edges: set[tuple[str, str]] = frozenset(),
    tier3: set[tuple[str, str]] = frozenset(),
) -> MagicMock:
    by_id = {r.id: r for r in records}
    graph = MagicMock()
    graph.get_record_by_id = AsyncMock(side_effect=lambda rid, *a, **k: by_id.get(rid))
    graph.get_records_by_virtual_record_id = AsyncMock(
        side_effect=lambda vrid, *a, **k: [r.id for r in records if r.virtual_record_id == vrid],
    )
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


_AS_ALICE = [Actor(org_id=_ORG, user_id=_ALICE)]


class TestAuthorizeAttachments:
    async def test_own_attachment_is_kept(self) -> None:  # T9
        graph = _graph([_record("r-alice")], edges={("ukey-alice", "r-alice")})
        atts = [_att("r-alice")]

        kept = await authorize_attachments(_AS_ALICE, atts, graph, _LOG)

        assert kept == atts

    async def test_other_users_attachment_is_dropped_and_logged_by_id_only(self, caplog) -> None:  # T10
        graph = _graph([_record("r-bob")], edges={("ukey-bob", "r-bob")})

        with caplog.at_level(logging.WARNING, logger=_LOG.name):
            kept = await authorize_attachments(_AS_ALICE, [_att("r-bob")], graph, _LOG)

        assert kept == []
        own_log = [r.getMessage() for r in caplog.records if r.name == _LOG.name]
        assert any("r-bob" in m for m in own_log)
        assert not any("secret-plan" in m for m in own_log)

    async def test_attachment_shared_through_reader_edge_is_kept(self) -> None:  # T11
        graph = _graph(
            [_record("r-bob")], edges={("ukey-bob", "r-bob"), ("ukey-alice", "r-bob")},
        )

        kept = await authorize_attachments(_AS_ALICE, [_att("r-bob")], graph, _LOG)

        assert [a["recordId"] for a in kept] == ["r-bob"]

    async def test_mixed_list_keeps_only_authorized_in_order(self) -> None:  # T12
        graph = _graph(
            [_record("r1"), _record("r2"), _record("r3"), _record("r4")],
            edges={("ukey-alice", "r1"), ("ukey-alice", "r4")},
            tier3={(_ALICE, "r3")},
        )
        atts = [_att("r1"), _att("r2"), _att("r3"), _att("r4")]

        kept = await authorize_attachments(_AS_ALICE, atts, graph, _LOG)

        assert [a["recordId"] for a in kept] == ["r1", "r3", "r4"]

    async def test_authorizer_error_drops_attachment_without_raising(self) -> None:  # T14
        graph = _graph([_record("r1"), _record("r2")], edges={("ukey-alice", "r2")})
        real_get_edge = graph.get_edge.side_effect

        async def _flaky(*, from_id: str, to_id: str, **kw: object) -> dict | None:
            if to_id == "r1":
                raise ConnectionError("graph down")
            return real_get_edge(from_id=from_id, to_id=to_id, **kw)

        graph.get_edge = AsyncMock(side_effect=_flaky)

        kept = await authorize_attachments(_AS_ALICE, [_att("r1"), _att("r2")], graph, _LOG)

        assert [a["recordId"] for a in kept] == ["r2"]

    async def test_own_record_id_paired_with_foreign_vrid_is_dropped(self) -> None:
        graph = _graph(
            [_record("r-alice"), _record("r-bob")], edges={("ukey-alice", "r-alice")},
        )
        forged = _att("r-alice", vrid="vr-r-bob")

        kept = await authorize_attachments(_AS_ALICE, [forged], graph, _LOG)

        assert kept == []

    async def test_cross_org_record_is_dropped(self) -> None:
        graph = _graph([_record("r1", org_id="org-2")], edges={("ukey-alice", "r1")})

        kept = await authorize_attachments(_AS_ALICE, [_att("r1")], graph, _LOG)

        assert kept == []
        graph.get_edge.assert_not_awaited()

    async def test_unknown_record_is_dropped(self) -> None:
        kept = await authorize_attachments(_AS_ALICE, [_att("ghost")], _graph([]), _LOG)

        assert kept == []

    async def test_vrid_only_attachment_resolves_to_a_readable_record(self) -> None:
        graph = _graph(
            [_record("r-bob", vrid="vr-shared"), _record("r-alice", vrid="vr-shared")],
            edges={("ukey-alice", "r-alice")},
        )

        kept = await authorize_attachments(_AS_ALICE, [_att(None, vrid="vr-shared")], graph, _LOG)

        assert len(kept) == 1

    async def test_vrid_only_attachment_without_readable_record_is_dropped(self) -> None:
        graph = _graph([_record("r-bob", vrid="vr-bob")], edges={("ukey-bob", "r-bob")})

        kept = await authorize_attachments(_AS_ALICE, [_att(None, vrid="vr-bob")], graph, _LOG)

        assert kept == []

    async def test_entries_without_ids_or_not_dicts_are_dropped(self) -> None:
        graph = _graph([])

        kept = await authorize_attachments(
            _AS_ALICE, [{"recordName": "x.pdf"}, "r1", None], graph, _LOG,
        )

        assert kept == []

    async def test_any_run_identity_may_grant(self) -> None:
        graph = _graph([_record("r-bob")], edges={("ukey-bob", "r-bob")})
        actors = [Actor(org_id=_ORG, user_id=_ALICE), Actor(org_id=_ORG, user_id=_BOB)]

        kept = await authorize_attachments(actors, [_att("r-bob")], graph, _LOG)

        assert [a["recordId"] for a in kept] == ["r-bob"]

    async def test_no_actors_denies_everything(self) -> None:
        graph = _graph([_record("r1")], edges={("ukey-alice", "r1")})

        assert await authorize_attachments([], [_att("r1")], graph, _LOG) == []


class TestAuthorizeQueryAttachments:
    async def test_history_attachments_go_through_the_same_gate(self) -> None:  # T13
        graph = _graph(
            [_record("r-own"), _record("r-bob"), _record("r-now")],
            edges={("ukey-alice", "r-own"), ("ukey-alice", "r-now")},
        )
        query_info = {
            "query": "summarize",
            "attachments": [_att("r-now")],
            "previous_conversations": [
                {"role": "user_query", "content": "look", "attachments": [_att("r-bob"), _att("r-own")]},
                {"role": "bot_response", "content": "ok"},
            ],
        }

        result = await authorize_query_attachments(query_info, _AS_ALICE, graph, _LOG)

        assert [a["recordId"] for a in result["attachments"]] == ["r-now"]
        history = result["previous_conversations"]
        assert [a["recordId"] for a in history[0]["attachments"]] == ["r-own"]
        assert history[0]["content"] == "look"
        assert history[1] == {"role": "bot_response", "content": "ok"}
        assert query_info["previous_conversations"][0]["attachments"][0]["recordId"] == "r-bob"

    async def test_no_attachments_touches_nothing(self) -> None:
        graph = _graph([])
        query_info = {"query": "hi", "previous_conversations": [{"role": "user_query", "content": "x"}]}

        result = await authorize_query_attachments(query_info, _AS_ALICE, graph, _LOG)

        assert result is query_info
        graph.get_record_by_id.assert_not_awaited()

    async def test_same_attachment_in_turn_and_history_is_checked_once(self) -> None:
        graph = _graph([_record("r1")], edges={("ukey-alice", "r1")})
        query_info = {
            "attachments": [_att("r1")],
            "previous_conversations": [{"role": "user_query", "attachments": [_att("r1")]}],
        }

        result = await authorize_query_attachments(query_info, _AS_ALICE, graph, _LOG)

        assert len(result["attachments"]) == 1
        assert len(result["previous_conversations"][0]["attachments"]) == 1
        assert graph.get_record_by_id.await_count == 1

    async def test_unexpected_failure_strips_every_attachment(self) -> None:
        graph = _graph([_record("r1")], edges={("ukey-alice", "r1")})
        query_info = {
            "attachments": [_att("r1")],
            "previous_conversations": [{"role": "user_query", "attachments": [_att("r1")]}],
        }

        result = await authorize_query_attachments(query_info, "not-a-list-of-actors", graph, _LOG)

        assert result["attachments"] == []
        assert result["previous_conversations"][0]["attachments"] == []


class TestActorsForRun:
    def test_run_identity_and_distinct_caller_are_both_actors(self) -> None:
        actors = actors_for_run(
            {"userId": "creator", "orgId": _ORG}, RunOwner(user_id="caller", org_id=_ORG),
        )

        assert actors == [
            Actor(org_id=_ORG, user_id="creator"), Actor(org_id=_ORG, user_id="caller"),
        ]

    def test_same_identity_is_not_duplicated_and_blanks_are_skipped(self) -> None:
        assert actors_for_run(
            {"userId": _ALICE, "orgId": _ORG}, RunOwner(user_id=_ALICE, org_id=_ORG),
        ) == _AS_ALICE
        assert actors_for_run({"userId": "", "orgId": _ORG}, None) == []
