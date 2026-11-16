"""Live start-up and storm/recover smoke: the real start-up binding, then an outage and recovery. Wall-clock time, a real Redis.

Nothing is simulated: the fleet state is bound the way ``start_kafka_consumers`` binds it (the retry tracker is the factory's own product
over a real Redis, the install secret comes from a configuration service, the trial renewer and the recovery leader's hold loop run),
twelve healthy documents go through real stage workers and a real OpenAI client to a fake provider on a socket, then the provider
answers 503 to everything for a while, then recovers. The breaker's cooldowns (30 s, then 60 s) are Redis time.

Expected: 0 FAILED, every document completes, the requests the fake receives in the storm are bounded by the burst that starts together and
the trials after it, and the breaker ends closed with no hold left. Needs ``SPEND_IT_REDIS_URL``; about 3 minutes.
"""

import asyncio
import logging
import time
from types import SimpleNamespace
from typing import ClassVar
from urllib.parse import unquote, urlparse

import pytest
from langchain_openai import ChatOpenAI

from app.config.constants.arangodb import ProgressStatus
from app.indexing_main import _bind_fleet_state
from app.modules.pipeline.coordinator import Coordinator
from app.modules.pipeline.models import (
    HeadlineField,
    Priority,
    StageFingerprint,
    StageJob,
    StageOutcome,
    StageResult,
    StageState,
    Workload,
)
from app.modules.pipeline.pause_budget import PauseLimits
from app.modules.pipeline.pause_judge import PauseJudge
from app.modules.pipeline.pause_store import PauseStore
from app.modules.pipeline.policy import DEFAULT_POLICY
from app.modules.pipeline.registry import StageRegistry
from app.modules.pipeline.worker import StageFailed, StageJobHandler, StagePaused
from app.services.llm_gateway.fleet_breaker import (
    FleetBreaker,
    credential_of,
    provider_pk,
    size_class,
)
from app.services.llm_gateway.fleet_hold import HoldKeeper, run_hold_leader
from app.services.llm_gateway.fleet_redis import bind_fleet_redis
from app.services.llm_gateway.gateway import LLMGateway, provider_key
from app.services.llm_gateway.trial_lease import get_trial_renewer
from app.services.messaging.config import IndexingEvent, RedisConfig, StreamMessage
from app.services.messaging.fail_safe_retry_tracker import FailSafeRetryTracker
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.model_identity.transport.instrument import instrument_model
from app.services.resource_governor.models import ParseTier
from app.utils.llm import LLMUnavailableError
from tests.support.fake_llm_server import OK, FakeLLMServer, Reply
from tests.support.spend_world import require_spend_redis
from tests.unit.modules.pipeline.fakes import (
    InMemoryStageStateStore,
    RecordingHeadlines,
    RecordingProducer,
    StubIO,
)

pytestmark = [pytest.mark.live_smoke, pytest.mark.timeout(600)]


class _RealClock:
    def __call__(self) -> int:
        return int(time.time() * 1000)


class _Config:
    def __init__(self, redis: RedisConfig | None = None) -> None:
        self.store: dict[str, str] = {}
        self.redis = redis or RedisConfig()

    async def get_redis_config(self) -> RedisConfig:
        return self.redis

    async def create_config_if_absent(self, key: str, value: str) -> bool:
        return self.store.setdefault(key, value) is value

    async def get_config(self, key: str, **_: object) -> str | None:
        return self.store.get(key)


class _Stage:
    name: ClassVar[str] = "classify"
    version: ClassVar[int] = 1
    requires: ClassVar[frozenset[str]] = frozenset({"embed"})
    workload: ClassVar[Workload] = Workload.LLM
    headline: ClassVar[HeadlineField | None] = HeadlineField.EXTRACTION
    budget_s: ClassVar[float] = 120.0

    def __init__(self, gateway: LLMGateway, model: ChatOpenAI) -> None:
        self.gateway, self.model = gateway, model

    def applies(self, view, policy) -> bool:
        return True

    async def fingerprint(self, job: StageJob, io) -> StageFingerprint:
        return StageFingerprint(stage=self.name, stage_version=1, input_digest=job.text_digest, config_digest="c")

    async def run(self, job: StageJob, io) -> StageResult:
        try:
            await self.gateway.invoke(self.model, f"DOC:{job.virtual_record_id}", provider=provider_key(self.model))
        except LLMUnavailableError as exc:
            return StageResult.paused_for(exc, f"Model provider unavailable: {exc}")
        return StageResult(outcome=StageOutcome.COMPLETED)


def _job(doc: str) -> StageJob:
    return StageJob.model_validate({
        "stage": "classify", "stage_version": 1, "org_id": "org-1", "virtual_record_id": doc, "rev": "r1", "record_ids": (f"rec-{doc}",),
        "connector_id": "c", "tier": ParseTier.LIGHT, "priority": Priority.BULK, "trigger": "embed", "text_digest": "t",
        "blocks_digest": "b", "text_chars": 10, "has_tables": False, "has_images": False,
    })


async def test_startup_storm_and_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    url = require_spend_redis()
    parsed = urlparse(url)
    monkeypatch.setenv("REDIS_MODE", "standalone")
    monkeypatch.delenv("KV_STORE_TYPE", raising=False)
    monkeypatch.delenv("MESSAGE_BROKER", raising=False)
    config = RedisConfig(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 6379,
        db=int((parsed.path or "/0").strip("/") or 0),
        password=unquote(parsed.password) if parsed.password else None,
        tls=parsed.scheme == "rediss",
    )
    log = logging.getLogger("smoke")

    # ---- start-up, as the service does it, with the real tracker beside it -------------------------------------------------
    tracker = MessagingFactory.create_retry_manager(log, config)
    assert isinstance(tracker, FailSafeRetryTracker)
    await tracker.initialize()
    container = SimpleNamespace(config_service=_Config)
    shared_config = _Config(config)
    container.config_service = lambda: shared_config
    fleet = await _bind_fleet_state(container, log)
    namespace = f"smoke{int(time.time())}"
    fleet._namespace = namespace

    breaker = FleetBreaker(fleet)
    notices: list[dict] = []

    async def send(doc: dict) -> bool:
        notices.append(doc)
        return True

    leader = asyncio.create_task(run_hold_leader(
        HoldKeeper(fleet, breaker, sender=send), lambda: asyncio.sleep(0, result=True), logger=log, interval_s=0.2
    ))
    assert get_trial_renewer().running

    server = await FakeLLMServer(lambda body: OK).start()
    storm = {"on": False}
    server.behavior = lambda body: Reply(status=503) if storm["on"] else OK
    model = instrument_model(ChatOpenAI(model="m", api_key="sk-smoke", base_url=server.base_url, max_retries=0, timeout=30), "openAI", None)
    gateway = LLMGateway(8)
    clock = _RealClock()
    states = InMemoryStageStateStore(clock)  # type: ignore[arg-type]
    headlines, producer = RecordingHeadlines(), RecordingProducer()
    registry = StageRegistry()
    registry.register_external("embed")
    registry.register(_Stage(gateway, model))
    store = PauseStore()
    coordinator = Coordinator(
        registry, states, headlines, producer, policy_for=lambda _o: DEFAULT_POLICY, logger=log, pause_store=store  # type: ignore[arg-type]
    )
    handler = StageJobHandler(
        registry, states, headlines, coordinator, StubIO, logger=log, clock_ms=clock, pause_judge=PauseJudge(store, limits=PauseLimits())
    )
    results: dict[str, str] = {}
    pending: list[asyncio.Task[None]] = []

    async def run_job(doc: str) -> None:
        job = _job(doc)
        states.put(StageState.from_job(job, ProgressStatus.QUEUED, clock()))
        message = StreamMessage(eventType="stageJob", payload=job.model_dump(mode="json", by_alias=True))
        while True:
            try:
                events = [e.event async for e in handler(message)]
                assert events[-1] is IndexingEvent.INDEXING_COMPLETE
                results[doc] = "OK"
                return
            except StagePaused as paused:
                await asyncio.sleep(min(float(paused.delay_s or 1.0), 2.0))  # a real wake, accelerated
            except StageFailed as failed:
                results[doc] = f"FAILED: {failed}"
                return

    try:
        # healthy traffic
        await asyncio.gather(*(run_job(f"calm-{i}") for i in range(12)))
        assert set(results.values()) == {"OK"} and sum(server.requests[f"calm-{i}"] for i in range(12)) == 12

        # the storm: the provider answers 503 to everything for 40 s, then recovers
        storm["on"] = True
        before = server.total
        pending.extend(asyncio.create_task(run_job(f"storm-{i}")) for i in range(12))
        await asyncio.sleep(40)
        storm["on"] = False
        during = server.total - before
        assert not [d for d, r in results.items() if r != "OK"], "a job FAILED during the storm"
        # the 12 documents start together, so up to 12 first requests are in flight before the fifth failure opens the breaker; then
        # only trials go out (the first is due after 30 s). Without the breaker the 2 s wakes would send about 240.
        assert during <= 12 + 3, f"{during} requests reached the provider during a 40 s storm"
        await asyncio.wait_for(asyncio.gather(*pending), timeout=300)

        failed = {d: r for d, r in results.items() if r != "OK"}
        assert not failed and len(results) == 24, failed
        pk = provider_pk(provider_key(model), credential_of(model))
        assert (await breaker.admit(pk, size_class(1), "after")).code == 0, "the breaker is closed again"
        assert await breaker.hold(pk, "s") is None
        assert [n for n in notices if n["type"].startswith("INDEXING_PROVIDER")] == [], "no hold: the documents are not poison"
    finally:
        # Jobs a failed assertion left running are cancelled and joined before the server and the fleet state they use go.
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        leader.cancel()
        await get_trial_renewer().stop()
        await server.stop()
        registry_obj = getattr(container, "fleet_redis_registry", None)
        client = fleet.client()
        keys = [k async for k in client.scan_iter(match=f"{namespace}:*")]
        if keys:
            await client.delete(*keys)
        bind_fleet_redis(None)
        if registry_obj is not None:
            await registry_obj.aclose()
        await tracker.cleanup()
