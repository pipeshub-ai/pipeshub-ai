"""``admission`` on the indexing and parsing ``/health``, and the parse route's rejection counters.

The endpoints are readable by anyone who can reach them: they carry counts only.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.api.routes import parsing as parsing_route
from app.services.messaging import consumer_concurrency as cc
from app.services.resource_governor.models import ParseTier
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

CANARY = "canary-7f3a"


def _consumer_with_backlog(calls: list[int]) -> SimpleNamespace:
    backlog = cc.AdmissionBacklog()
    for _ in range(3):
        backlog.add(ParseTier.HEAVY, cc.PHASE_GATE, None, SimpleNamespace(marker=CANARY))
    host = SimpleNamespace(
        governor=None, stage_admission=None, priority_lane=None, gate_waiters=cc.GateWaiters(),
        admission_backlog=backlog, concurrency_manager=None,
    )

    def explain() -> dict:
        calls.append(1)
        return cc.admission_explain(host)

    return SimpleNamespace(admission_explain=explain, dispatch_stats=lambda: {})


def _request(governor: object) -> MagicMock:
    request = MagicMock()
    request.app.state = MagicMock()
    request.app.state.governor = governor
    del request.app.state.pipeline_runtime
    return request


@pytest.mark.asyncio
class TestIndexingHealth:
    """"""

    async def test_admission_is_present_and_carries_counts_only(self) -> None:
        from app.indexing_main import health_check

        seen: list[int] = []
        governor = make_test_governor()
        try:
            with patch("app.indexing_main.container") as container:
                container.kafka_consumers = [("records", _consumer_with_backlog(seen))]
                container.worker_loop = None
                response = await health_check(_request(governor))
        finally:
            governor.close()
        body = json.loads(response.body)
        text = response.body.decode()
        assert body["admission"]["records"]["v"] == 2
        assert body["admission"]["records"]["backlog"]["tiers"]["heavy"]["atGate"] == 3
        assert body["resource_governor"]["admission"]["v"] == 2
        assert body["openCircuits"] == sorted(set(body["openCircuits"]))
        assert seen == [1]
        assert CANARY not in text

    async def test_the_explain_never_serialises_what_the_backlog_was_told_about_a_message(self) -> None:
        """Negative control: the message handed to the recorder is never copied into the output."""
        consumer = _consumer_with_backlog([])
        assert CANARY not in json.dumps(consumer.admission_explain())

    async def test_a_failing_explain_does_not_fail_health(self) -> None:
        from app.indexing_main import health_check

        broken = SimpleNamespace(admission_explain=MagicMock(side_effect=RuntimeError("SENTINEL")), dispatch_stats=lambda: {})
        with patch("app.indexing_main.container") as container:
            container.kafka_consumers = [("records", broken)]
            container.worker_loop = None
            response = await health_check(_request(None))
        assert response.status_code == 200
        assert json.loads(response.body)["admission"]["records"] == {"error": "unavailable"}
        assert "SENTINEL" not in response.body.decode()


    async def test_a_failing_circuit_walk_does_not_fail_health(self) -> None:
        from app.indexing_main import health_check

        with patch("app.indexing_main.container") as container, patch(
            "app.indexing_main.open_circuits", side_effect=RuntimeError("SENTINEL")
        ):
            container.kafka_consumers = []
            container.worker_loop = None
            response = await health_check(_request(None))
        assert response.status_code == 200
        assert json.loads(response.body)["openCircuits"] == {"error": "unavailable"}
        assert "SENTINEL" not in response.body.decode()
        container.logger().warning.assert_called()


@pytest.mark.asyncio
class TestParsingHealth:
    async def test_admission_has_governor_and_parse_sections(self) -> None:
        from app.parsing_main import app as parsing_app
        from app.parsing_main import health_check

        registry = MagicMock()
        registry.list_all_formats.return_value = {}
        governor = make_test_governor()
        try:
            with (
                patch.object(parsing_app.state, "parser_registry", registry, create=True),
                patch.object(parsing_app.state, "governor", governor, create=True),
                patch("app.parsing_main.container"),
            ):
                body = json.loads((await health_check()).body)
        finally:
            governor.close()
        assert body["admission"]["governor"]["v"] == 2
        assert set(body["admission"]["parse"]["rejected"]) == {"heavy", "light"}


class TestRejectionCounters:
    """"""

    def test_each_reason_counts_by_tier(self) -> None:
        before = parsing_route.parse_admission_stats()["rejected"]
        for reason in ("gate_timeout", "over_share", "memory_excess", "lane_timeout"):
            parsing_route._count_rejection(ParseTier.LIGHT, reason)
        parsing_route._count_rejection(ParseTier.HEAVY, "over_share")
        after = parsing_route.parse_admission_stats()["rejected"]
        for reason in ("gate_timeout", "over_share", "memory_excess", "lane_timeout"):
            assert after["light"][reason] == before["light"][reason] + 1
        assert after["heavy"]["over_share"] == before["heavy"]["over_share"] + 1
        assert after["heavy"]["gate_timeout"] == before["heavy"]["gate_timeout"]

    def test_a_route_that_only_counted_the_gate_would_miss_the_others(self) -> None:
        """Negative control: the stats of an untouched reason stay at what they were."""
        before = parsing_route.parse_admission_stats()["rejected"]["heavy"]["lane_timeout"]
        parsing_route._count_rejection(ParseTier.HEAVY, "gate_timeout")
        assert parsing_route.parse_admission_stats()["rejected"]["heavy"]["lane_timeout"] == before
