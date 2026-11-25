"""Usage telemetry v3: route choice, retries, fall-back and totals, against a fake collector."""

import asyncio
import contextlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.plugins.telemetry.event_buffer import EventBuffer, event_buffer
from app.plugins.telemetry.pusher import (
    _PROCESS_ID,
    REQUIRED_CONFIG_FIELDS,
    MetricsPusher,
)
from app.plugins.telemetry.v3 import edition
from app.plugins.telemetry.v3.sender import (
    PROCESS_ID_RE,
    HttpAnswer,
    Mode,
    Route,
    Secret,
    UsageV3Sender,
    V3Target,
    contract_mode,
)
from tests.unit.telemetry.usage_v3_fakes import (
    ANSWER_LOST,
    EVENTS_URL,
    METRICS_URL,
    TIMEOUT,
    V3,
    Clock,
    FakeCollector,
)

INSTALL = "install-1"
SECRET = "s3cr3t-Zm9vYmFyYmF6cXV4-0123456789abcdefghij"
ACTIVITY = "pipeshub.usage.activity{activity=document_indexed,connector_type=slack,status=ok}"
PERSONAL = ("org-6f1d", "user-99aa", "ada@example-corp.com", "example-corp.com")


class Registry:
    """A registry the test drives: one activity counter per org, and one gauge."""

    def __init__(self) -> None:
        self.counts: dict[str, float] = {}
        self.records = 0.0

    def inc(self, n: float = 1, org: str = PERSONAL[0]) -> None:
        self.counts[org] = self.counts.get(org, 0) + n

    def reset(self) -> None:
        self.counts.clear()

    def snapshot(self):
        samples = [
            ("pipeshub_activity_total", "counter", {
                "service": "indexing_service", "activity": "document_indexed", "connector": "slack",
                "status": "ok", "org": org, "kb": "kb", "domain": PERSONAL[3], "mimetype": "text/plain",
            }, value)
            for org, value in self.counts.items()
        ]
        samples.append(("pipeshub_http_requests_total", "counter", {"route": "/search"}, 99.0))
        if self.records:
            samples.append(("pipeshub_usage_records", "gauge", {
                "connector_type": "slack", "indexing_status": "completed", "org": PERSONAL[0], "domain": PERSONAL[3],
            }, self.records))
        return samples


def search_event(i: int = 0) -> dict:
    return {
        "event": "search_performed",
        "timestamp": "2026-11-25T08:00:00+00:00",
        "props": {"orgId": PERSONAL[0], "userId": PERSONAL[1], "email": PERSONAL[2], "domain": PERSONAL[3],
                  "status_code": 200, "num_queries": i, "search_type": "hybrid"},
    }


class Rig:
    def __init__(self, mode=Mode.AUTO, collector=None, process_id="worker-1", registry=None, clock=None, events=None):
        self.collector = collector if collector is not None else FakeCollector({INSTALL: SECRET})
        self.registry = registry if registry is not None else Registry()
        self.clock = clock if clock is not None else Clock()
        self.events = events if events is not None else EventBuffer()
        self.logger = MagicMock()
        self.sender = UsageV3Sender(
            mode=mode, service="query_service", process_id=process_id, http=self.collector,
            snapshot=self.registry.snapshot, events=self.events, logger=self.logger, clock=self.clock, edition="oss",
        )

    def target(self, secret: str | None = SECRET, version: str | None = "1.4.0") -> V3Target:
        return V3Target(METRICS_URL, EVENTS_URL, INSTALL, Secret(secret) if secret else None, version)

    async def tick(self, **kwargs) -> Route:
        return await self.sender.tick(self.target(**kwargs))


class TestMode:
    def test_contract_mode(self, monkeypatch):
        monkeypatch.delenv("USAGE_TELEMETRY_CONTRACT", raising=False)
        assert contract_mode() is Mode.LEGACY
        for raw, mode in (("v3", Mode.V3), (" LEGACY ", Mode.LEGACY), (" Auto ", Mode.AUTO), ("v2", Mode.LEGACY), ("", Mode.LEGACY)):
            monkeypatch.setenv("USAGE_TELEMETRY_CONTRACT", raw)
            assert contract_mode() is mode

    def test_edition(self, monkeypatch):
        monkeypatch.delenv("USAGE_TELEMETRY_EDITION", raising=False)
        assert edition() == "oss"
        for raw, expected in ((" EE ", "ee"), ("ee", "ee"), ("enterprise", "oss"), ("", "oss")):
            monkeypatch.setenv("USAGE_TELEMETRY_EDITION", raw)
            assert edition() == expected

    def test_process_id_fits_the_contract(self):
        assert PROCESS_ID_RE.match(_PROCESS_ID)
        with pytest.raises(ValueError):
            Rig(process_id="not valid!")


class TestSendAndAck:
    async def test_sends_deltas_gauges_and_events_and_drops_the_batch_on_ack(self):
        rig = Rig()
        rig.registry.inc(3)
        rig.registry.inc(4, org="org-2")
        rig.registry.records = 40
        rig.events.enqueue(search_event(2))
        rig.events.enqueue({"event": "answer_generated", "timestamp": "t", "props": {"orgId": PERSONAL[0]}})

        assert await rig.tick() is Route.V3

        metrics, events = rig.collector.bodies(METRICS_URL), rig.collector.bodies(EVENTS_URL)
        assert len(metrics) == 1 and len(events) == 1
        body = metrics[0]
        assert {k: body[k] for k in body if k != "points"} == {
            "processId": "worker-1", "sequence": 1, "service": "query_service", "version": "1.4.0", "edition": "oss",
        }
        assert body["points"][0] == {
            "name": "pipeshub.usage.activity", "type": "counter",
            "labels": {"activity": "document_indexed", "connector_type": "slack", "status": "ok"}, "value": 7,
        }
        gauge = body["points"][1]
        assert gauge["name"] == "pipeshub.usage.records" and gauge["type"] == "gauge" and gauge["value"] == 40
        assert gauge["ts"] == int(rig.clock.now * 1000)
        assert len(body["points"]) == 2  # the HTTP counter is not a usage signal
        assert events[0]["events"] == [{
            "name": "search.performed", "ts": "2026-11-25T08:00:00+00:00",
            "attributes": {"search_type": "hybrid", "status_code": 200, "num_queries": 2},
        }]
        assert events[0]["sequence"] == 1
        assert rig.collector.requests[0][2] == f"{INSTALL}.{SECRET}"
        assert rig.collector.totals == {ACTIVITY: 7}
        assert rig.events.size() == 0

        # Nothing new: the gauge is sent again as it is, no counter point, no events request.
        assert await rig.tick() is Route.V3
        second = rig.collector.bodies(METRICS_URL)[1]
        assert second["sequence"] == 2
        assert [p["type"] for p in second["points"]] == ["gauge"]
        assert len(rig.collector.bodies(EVENTS_URL)) == 1
        assert rig.collector.totals == {ACTIVITY: 7}

    async def test_no_request_when_there_is_nothing_to_send(self):
        rig = Rig()
        assert await rig.tick() is Route.V3  # one empty batch finds out what the collector speaks
        assert rig.collector.bodies() == [
            {"processId": "worker-1", "sequence": 1, "service": "query_service", "version": "1.4.0",
             "edition": "oss", "points": []},
        ]
        assert await rig.tick() is Route.V3
        assert len(rig.collector.requests) == 1

    async def test_version_that_does_not_fit_is_left_out(self):
        rig = Rig()
        await rig.tick(version="1.4.0 (local build)")
        assert "version" not in rig.collector.bodies()[0]

    async def test_nothing_personal_is_sent(self):
        rig = Rig()
        rig.registry.inc(3)
        rig.registry.records = 5
        for i in range(3):
            rig.events.enqueue(search_event(i))
        await rig.tick()
        sent = b"".join(body for _, body, _ in rig.collector.requests).decode()
        for value in (*PERSONAL, "orgId", "userId", "email", "domain", '"org"', '"kb"', "mimetype", INSTALL, SECRET):
            assert value not in sent

    async def test_at_most_2000_events_per_batch(self):
        rig = Rig()
        for i in range(2500):
            rig.events.enqueue(search_event(i))
        await rig.tick()
        assert len(rig.collector.events) == 2000
        assert rig.events.size() == 500
        await rig.tick()
        assert len(rig.collector.events) == 2500
        assert [e["attributes"]["num_queries"] for e in rig.collector.events] == list(range(2500))


class TestRetry:
    @pytest.mark.parametrize("failure", [HttpAnswer(status=502, **V3), HttpAnswer(status=500, **V3), TIMEOUT, ANSWER_LOST])
    async def test_same_body_again_and_counted_once(self, failure):
        rig = Rig()
        rig.registry.inc(5)
        rig.collector.script.append(failure)

        assert await rig.tick() is Route.HOLD
        rig.registry.inc(2)  # counted while the batch waits: stays in the registry
        rig.clock.advance(31)
        assert await rig.tick() is Route.V3

        first, second = rig.collector.requests[0], rig.collector.requests[1]
        assert first[1] == second[1]  # byte for byte, same sequence
        assert json.loads(second[1])["sequence"] == 1
        assert rig.collector.totals == {ACTIVITY: 5}

        assert await rig.tick() is Route.V3
        assert rig.collector.bodies(METRICS_URL)[-1]["sequence"] == 2
        assert rig.collector.totals == {ACTIVITY: 7}

    async def test_events_batch_is_retried_whole(self):
        rig = Rig()
        rig.events.enqueue(search_event(1))
        rig.collector.script.extend([None, HttpAnswer(status=502, **V3)])  # metrics fine, events fail

        assert await rig.tick() is Route.HOLD
        rig.events.enqueue(search_event(2))
        rig.clock.advance(31)
        assert await rig.tick() is Route.V3

        bodies = [body for url, body, _ in rig.collector.requests if url == EVENTS_URL]
        assert bodies[0] == bodies[1]
        assert [e["attributes"]["num_queries"] for e in rig.collector.events] == [1]
        await rig.tick()
        assert [e["attributes"]["num_queries"] for e in rig.collector.events] == [1, 2]

    async def test_backoff_doubles_and_is_capped(self):
        rig = Rig()
        rig.registry.inc()
        rig.collector.script.extend([HttpAnswer(status=503, **V3)] * 9)
        waits = []
        for _ in range(9):
            sent = len(rig.collector.requests)
            assert await rig.tick() is Route.HOLD
            assert len(rig.collector.requests) == sent + 1
            wait = 0
            while True:  # how long until the sender tries again
                rig.clock.advance(15)
                wait += 15
                if rig.sender._retry_at <= rig.clock.now:
                    break
            waits.append(wait)
        assert waits == [30, 60, 120, 240, 480, 960, 1800, 1800, 1800]

    async def test_nothing_is_sent_before_the_backoff_ends(self):
        rig = Rig()
        rig.registry.inc()
        rig.collector.script.append(HttpAnswer(status=503, **V3))
        await rig.tick()
        rig.clock.advance(29)
        assert await rig.tick() is Route.HOLD
        assert len(rig.collector.requests) == 1

    async def test_retry_after_is_honoured_when_larger(self):
        rig = Rig()
        rig.registry.inc()
        rig.collector.script.append(HttpAnswer(status=429, retry_after_s=120, **V3))
        assert await rig.tick() is Route.HOLD
        rig.clock.advance(119)
        assert await rig.tick() is Route.HOLD
        assert len(rig.collector.requests) == 1
        rig.clock.advance(2)
        assert await rig.tick() is Route.V3
        assert rig.collector.totals == {ACTIVITY: 1}

    async def test_smaller_retry_after_does_not_shorten_the_backoff(self):
        rig = Rig()
        rig.registry.inc()
        rig.collector.script.append(HttpAnswer(status=429, retry_after_s=1, **V3))
        await rig.tick()
        rig.clock.advance(5)
        assert await rig.tick() is Route.HOLD
        assert len(rig.collector.requests) == 1

    @pytest.mark.parametrize("status", [400, 413])
    async def test_a_batch_that_can_never_be_accepted_is_dropped(self, status):
        rig = Rig()
        rig.registry.inc(5)
        rig.collector.script.append(HttpAnswer(status=status, detail="Invalid payload", **V3))

        assert await rig.tick() is Route.V3
        rig.logger.warning.assert_called_once()
        rig.registry.inc(1)
        assert await rig.tick() is Route.V3

        assert [b["sequence"] for b in rig.collector.bodies(METRICS_URL)] == [1, 2]
        assert rig.collector.totals == {ACTIVITY: 1}

    async def test_a_port_that_raises_is_no_answer(self):
        rig = Rig()
        rig.registry.inc()
        rig.collector.post = AsyncMock(side_effect=RuntimeError(f"Bearer {INSTALL}.{SECRET}"))
        assert await rig.tick() is Route.HOLD
        assert SECRET not in str(rig.logger.mock_calls)


class TestFallback:
    async def test_header_less_404_falls_back_caches_and_probes_again_after_24h(self):
        rig = Rig(collector=FakeCollector({INSTALL: SECRET}, v3=False))
        rig.registry.inc(5)
        rig.events.enqueue(search_event(1))

        assert await rig.tick() is Route.LEGACY
        assert len(rig.collector.requests) == 1
        assert rig.events.size() == 1  # still there for the legacy events endpoint

        rig.clock.advance(23 * 3600)
        assert await rig.tick() is Route.LEGACY
        assert len(rig.collector.requests) == 1  # decision cached: no v3 request

        # The collector is upgraded; after 24 hours the sender tries v3 again.
        rig.collector.v3 = True
        rig.registry.inc(2)
        rig.clock.advance(3600 + 1)
        assert await rig.tick() is Route.V3
        # Only what was counted since the last legacy push, which carried the first 5.
        assert rig.collector.totals == {ACTIVITY: 2}

    async def test_a_login_page_is_not_a_v3_collector(self):
        rig = Rig()
        rig.collector.script.append(HttpAnswer(status=200, detail="<html>Sign in</html>"))
        assert await rig.tick() is Route.LEGACY

    async def test_pending_events_go_back_for_legacy_with_their_original_props(self):
        rig = Rig()
        originals = [search_event(1), {"event": "answer_generated", "timestamp": "t", "props": {"orgId": "o"}}]
        for event in originals:
            rig.events.enqueue(event)
        rig.collector.script.extend([None, TIMEOUT])  # the events batch is left pending
        assert await rig.tick() is Route.HOLD
        assert rig.events.size() == 0
        rig.events.enqueue(search_event(3))

        rig.collector.v3 = False
        rig.clock.advance(31)
        assert await rig.tick() is Route.LEGACY
        assert rig.events.drain() == [*originals, search_event(3)]

    async def test_legacy_delivery_rebases_so_v3_does_not_resend_history(self):
        rig = Rig()
        rig.registry.inc(10)
        assert await rig.tick(secret=None) is Route.LEGACY  # Node has not registered yet
        rig.registry.inc(5)
        assert await rig.tick(secret=None) is Route.LEGACY
        assert rig.collector.requests == []

        rig.registry.inc(1)
        assert await rig.tick() is Route.V3
        assert rig.collector.totals == {ACTIVITY: 1}

    async def test_pending_metrics_batch_is_dropped_on_fallback(self):
        rig = Rig()
        rig.registry.inc(5)
        rig.collector.script.append(TIMEOUT)
        assert await rig.tick() is Route.HOLD

        rig.collector.v3 = False
        rig.clock.advance(31)
        assert await rig.tick() is Route.LEGACY  # legacy's cumulative push carries the 5

        rig.collector.v3 = True
        rig.registry.inc(2)
        rig.clock.advance(24 * 3600 + 1)
        assert await rig.tick() is Route.V3
        assert rig.collector.totals == {ACTIVITY: 2}
        assert rig.collector.bodies(METRICS_URL)[-1]["sequence"] == 2

    async def test_registry_reset_counts_from_zero(self):
        rig = Rig()
        rig.registry.inc(10)
        await rig.tick()
        rig.registry.reset()
        rig.registry.inc(3)
        await rig.tick()
        assert rig.collector.totals == {ACTIVITY: 13}


class TestModes:
    async def test_legacy_mode_never_calls_v3(self):
        rig = Rig(mode=Mode.LEGACY)
        rig.registry.inc(5)
        rig.events.enqueue(search_event())
        for _ in range(3):
            assert await rig.tick() is Route.LEGACY
            rig.clock.advance(300)
        assert rig.collector.requests == []
        assert rig.events.size() == 1

    async def test_v3_mode_never_asks_for_legacy(self):
        rig = Rig(mode=Mode.V3, collector=FakeCollector({INSTALL: SECRET}, v3=False))
        rig.registry.inc(5)
        rig.events.enqueue(search_event())

        assert await rig.tick(secret=None) is Route.HOLD  # no secret: wait, keep counting
        assert rig.collector.requests == []

        routes = set()
        for _ in range(6):  # not a v3 collector, then errors, then a refused credential
            routes.add(await rig.tick())
            rig.clock.advance(3600)
        rig.collector.v3 = True
        rig.collector.script.extend([HttpAnswer(status=503, **V3), HttpAnswer(status=401, **V3)] * 3)
        for _ in range(6):
            routes.add(await rig.tick())
            rig.clock.advance(3600)
        assert routes == {Route.HOLD}

        # It keeps trying v3, and nothing was lost meanwhile.
        assert await rig.tick() is Route.V3
        assert rig.collector.totals == {ACTIVITY: 5}
        assert len(rig.collector.events) == 1

    async def test_no_secret_in_auto_is_legacy(self):
        rig = Rig()
        assert await rig.tick(secret=None) is Route.LEGACY
        assert await rig.tick(secret="") is Route.LEGACY
        assert rig.collector.requests == []


class TestUnauthorised:
    async def test_a_rotated_secret_is_picked_up_on_the_next_tick(self):
        rig = Rig()
        rig.registry.inc(5)
        rig.collector.installs[INSTALL] = "rotated-secret-000000000000000000000000000"

        assert await rig.tick() is Route.HOLD  # first 401: no fall-back, batch kept
        rig.registry.inc(1)
        assert await rig.tick(secret="rotated-secret-000000000000000000000000000") is Route.V3

        assert rig.collector.requests[0][1] == rig.collector.requests[1][1]
        assert rig.collector.totals == {ACTIVITY: 5}
        await rig.tick(secret="rotated-secret-000000000000000000000000000")
        assert rig.collector.totals == {ACTIVITY: 6}
        rig.logger.warning.assert_not_called()

    async def test_three_in_a_row_is_not_v3_for_24_hours(self):
        rig = Rig()
        rig.registry.inc(5)
        rig.collector.installs[INSTALL] = "another"

        assert await rig.tick() is Route.HOLD
        assert await rig.tick() is Route.HOLD
        assert await rig.tick() is Route.LEGACY
        rig.logger.warning.assert_called_once()
        assert SECRET not in str(rig.logger.mock_calls)

        rig.clock.advance(3600)
        assert await rig.tick() is Route.LEGACY
        assert len(rig.collector.requests) == 3

        rig.collector.installs[INSTALL] = SECRET
        rig.registry.inc(2)
        rig.clock.advance(24 * 3600)
        assert await rig.tick() is Route.V3
        assert rig.collector.totals == {ACTIVITY: 2}

    async def test_an_accepted_push_between_two_401s_starts_the_count_again(self):
        rig = Rig()
        refused = HttpAnswer(status=401, **V3)
        rig.collector.script.extend([refused, refused, None, refused, refused])
        routes = []
        for _ in range(5):
            rig.registry.inc()
            routes.append(await rig.tick())
        assert Route.LEGACY not in routes


class TestProcesses:
    async def test_a_restarted_process_starts_at_one_and_totals_stay_right(self):
        collector, registry = FakeCollector({INSTALL: SECRET}), Registry()
        first = Rig(collector=collector, registry=registry, process_id="pid1-aaaa")
        registry.inc(5)
        await first.tick()
        registry.inc(2)
        await first.tick()

        fresh = Registry()  # the new process's registry starts empty
        second = Rig(collector=collector, registry=fresh, process_id="pid2-bbbb")
        fresh.inc(4)
        await second.tick()

        sent = [(b["processId"], b["sequence"]) for b in collector.bodies(METRICS_URL)]
        assert sent == [("pid1-aaaa", 1), ("pid1-aaaa", 2), ("pid2-bbbb", 1)]
        assert collector.totals == {ACTIVITY: 11}

    async def test_workers_add_up(self):
        collector = FakeCollector({INSTALL: SECRET})
        rigs = [Rig(collector=collector, process_id=f"worker-{i}") for i in range(3)]
        for n, rig in enumerate(rigs, start=1):
            rig.registry.inc(n)
            rig.events.enqueue(search_event(n))
        collector.script.append(ANSWER_LOST)  # one worker's first push is retried
        await asyncio.gather(*(rig.tick() for rig in rigs))
        for rig in rigs:
            rig.clock.advance(31)
            rig.registry.inc(10)
            await rig.tick()
            await rig.tick()

        assert collector.totals == {ACTIVITY: 1 + 2 + 3 + 30}
        assert sorted(e["attributes"]["num_queries"] for e in collector.events) == [1, 2, 3]


class TestSecretIsNeverLogged:
    async def test_no_log_call_and_no_repr_shows_it(self):
        rig = Rig()
        rig.registry.inc()
        rig.events.enqueue(search_event())
        leaky = f"refused credential Bearer {INSTALL}.{SECRET}"
        rig.collector.script.extend([
            HttpAnswer(status=400, detail=leaky, **V3),
            HttpAnswer(status=413, detail=leaky, **V3),
            HttpAnswer(status=503, detail=leaky, **V3),
            HttpAnswer(status=429, retry_after_s=5, detail=leaky, **V3),
            TIMEOUT,
            HttpAnswer(status=401, detail=leaky, **V3),
            HttpAnswer(status=401, detail=leaky, **V3),
            HttpAnswer(status=401, detail=leaky, **V3),
            HttpAnswer(status=404, detail=leaky),
        ])
        for _ in range(12):
            rig.registry.inc()
            rig.events.enqueue(search_event())
            await rig.tick()
            rig.clock.advance(2 * 3600)
        rig.clock.advance(24 * 3600)
        await rig.tick()

        assert rig.logger.mock_calls  # something was logged
        assert SECRET not in str(rig.logger.mock_calls)
        target = rig.target()
        assert SECRET not in repr(target) and SECRET not in str(target) and SECRET not in f"{target.secret}"


def node_config(**overrides) -> dict:
    return {
        "serverUrl": "https://collector.test/collect-metrics",
        "installId": INSTALL,
        "apiKey": "k",
        "appVersion": "1.4.0",
        "pushIntervalMs": "300000",
        "enableMetricCollection": "true",
        **overrides,
    }


def make_pusher(config: dict, collector: FakeCollector, monkeypatch, mode: str | None = "auto") -> MetricsPusher:
    """``mode`` is the setting; ``None`` leaves it unset. v3 is opt-in, so these tests opt in by default."""
    if mode is None:
        monkeypatch.delenv("USAGE_TELEMETRY_CONTRACT", raising=False)
    else:
        monkeypatch.setenv("USAGE_TELEMETRY_CONTRACT", mode)
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value=config)
    config_service.set_config = AsyncMock()
    pusher = MetricsPusher(config_service, "query_service", MagicMock(), usage_http=collector)
    pusher._push = AsyncMock()
    pusher._ship_events = AsyncMock()
    return pusher


async def run_ticks(pusher: MetricsPusher, ticks: int = 1) -> None:
    sleeps = [None] * (ticks - 1) + [asyncio.CancelledError]
    with (
        patch("app.plugins.telemetry.pusher.set_metric_collection_enabled"),
        patch("asyncio.sleep", AsyncMock(side_effect=sleeps)),
    ):
        with contextlib.suppress(asyncio.CancelledError):
            await pusher._run()


class TestPusherWiring:
    @pytest.fixture(autouse=True)
    def _empty_buffer(self):
        event_buffer.drain()
        yield
        event_buffer.drain()

    def test_install_secret_is_not_a_required_field(self):
        assert "installSecret" not in REQUIRED_CONFIG_FIELDS

    async def test_auto_with_a_secret_sends_v3_and_not_legacy(self, monkeypatch):
        collector = FakeCollector({INSTALL: SECRET})
        pusher = make_pusher(node_config(installSecret=SECRET), collector, monkeypatch)
        event_buffer.enqueue(search_event(1))

        await run_ticks(pusher, 2)

        urls = [url for url, _, _ in collector.requests]
        assert urls[0] == METRICS_URL and EVENTS_URL in urls
        assert collector.requests[0][2] == f"{INSTALL}.{SECRET}"
        assert collector.bodies(METRICS_URL)[0]["processId"] == _PROCESS_ID
        assert collector.bodies(METRICS_URL)[0]["version"] == "1.4.0"
        assert len(collector.events) == 1
        pusher._push.assert_not_called()
        pusher._ship_events.assert_not_called()
        pusher._config_service.set_config.assert_not_called()  # Python never writes the config
        assert SECRET not in str(pusher._logger.mock_calls)

    async def test_auto_without_a_secret_is_legacy(self, monkeypatch):
        collector = FakeCollector({INSTALL: SECRET})
        pusher = make_pusher(node_config(), collector, monkeypatch)
        await run_ticks(pusher)
        assert collector.requests == []
        pusher._push.assert_called_once()
        pusher._ship_events.assert_called_once()

    async def test_auto_falls_back_to_legacy_on_an_old_collector(self, monkeypatch):
        collector = FakeCollector(v3=False)
        pusher = make_pusher(node_config(installSecret=SECRET), collector, monkeypatch)
        await run_ticks(pusher, 2)
        assert len(collector.requests) == 1
        assert pusher._push.call_count == 2
        assert pusher._ship_events.call_count == 2

    async def test_legacy_mode_never_calls_v3(self, monkeypatch):
        collector = FakeCollector({INSTALL: SECRET})
        pusher = make_pusher(node_config(installSecret=SECRET), collector, monkeypatch, mode="legacy")
        await run_ticks(pusher, 2)
        assert collector.requests == []
        assert pusher._push.call_count == 2

    async def test_unset_setting_is_legacy_and_never_calls_v3(self, monkeypatch):
        collector = FakeCollector({INSTALL: SECRET})
        pusher = make_pusher(node_config(installSecret=SECRET), collector, monkeypatch, mode=None)
        await run_ticks(pusher, 2)
        assert collector.requests == []
        assert pusher._push.call_count == 2
        assert pusher._ship_events.call_count == 2

    async def test_v3_mode_never_calls_legacy(self, monkeypatch):
        collector = FakeCollector(v3=False)
        for config in (node_config(), node_config(installSecret=SECRET)):
            pusher = make_pusher(config, collector, monkeypatch, mode="v3")
            await run_ticks(pusher, 2)
            pusher._push.assert_not_called()
            pusher._ship_events.assert_not_called()

    async def test_nothing_on_either_contract_without_consent(self, monkeypatch):
        collector = FakeCollector({INSTALL: SECRET})
        for mode in ("auto", "v3", "legacy"):
            pusher = make_pusher(
                node_config(installSecret=SECRET, enableMetricCollection="false"), collector, monkeypatch, mode=mode
            )
            await run_ticks(pusher, 2)
            pusher._push.assert_not_called()
            pusher._ship_events.assert_not_called()
        assert collector.requests == []

    async def test_loaded_config_does_not_show_the_secret(self, monkeypatch):
        pusher = make_pusher(node_config(installSecret=SECRET), FakeCollector(), monkeypatch)
        cfg = await pusher._load_config()
        assert cfg["install_secret"].reveal() == SECRET
        assert SECRET not in repr(cfg) and SECRET not in str(cfg)
        target = pusher._v3_target(cfg)
        assert (target.metrics_url, target.events_url) == (METRICS_URL, EVENTS_URL)
