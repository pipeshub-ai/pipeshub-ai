"""A real OpenAI-style client over a real socket, through the real wire hook, gateway, breaker and worker."""

from typing import ClassVar

import pytest
from langchain_openai import ChatOpenAI

from app.modules.pipeline.models import StageOutcome, StageResult
from app.services.llm_gateway.gateway import LLMGateway, provider_key
from app.services.model_identity.transport.instrument import instrument_model
from app.utils.llm import LLMUnavailableError
from tests.support.fake_llm_server import HANG, OK, FakeLLMServer
from tests.support.gateway_world import GatewayWorld, ModelStage, patch_gateway_ceiling

pytestmark = pytest.mark.timeout(300)


class _OpenAIStage(ModelStage):
    """Like classification: the document is in the prompt, the model is a real client with its retries off."""

    name: ClassVar[str] = "classify"

    def __init__(self, gateway: LLMGateway, model: ChatOpenAI) -> None:
        self.gateway, self.model = gateway, model

    async def run(self, job, io) -> StageResult:
        try:
            await self.gateway.invoke(self.model, f"DOC:{job.virtual_record_id}", provider=provider_key(self.model))
        except LLMUnavailableError as exc:
            return StageResult.paused_for(exc, f"Model provider unavailable: {exc}")
        return StageResult(outcome=StageOutcome.COMPLETED)


async def test_poison_document_request_count_is_six(fleet, sim_clock, monkeypatch) -> None:
    """The fake hangs on document X and answers the others. Among healthy traffic X's failed sends are charged, so it is
    FAILED after exactly 6 requests, with the reason, and no healthy document is failed. ``max_retries=0`` is set
    explicitly: the pause budget does not change the SDK's own retries."""
    patch_gateway_ceiling(monkeypatch, 2.5)
    server = await FakeLLMServer(lambda body: HANG if "DOC:poison" in body else OK).start()
    try:
        model = instrument_model(
            ChatOpenAI(model="m", api_key="x", base_url=server.base_url, max_retries=0, timeout=30), "openAI", None
        )
        world = GatewayWorld(fleet, sim_clock, stage_factory=lambda gateway: _OpenAIStage(gateway, model))
        # a steady stream of other work on the same key, running before the poison document arrives (the key's
        # success rate needs 20 outcomes) and until it is done
        for i in range(600):
            world.add_job(f"healthy-{i}", arrive_s=i * 3.0)
        world.add_job("poison", arrive_s=100.0)
        outcomes = await world.run()
    finally:
        await server.stop()
    assert outcomes["poison"].status == "FAILED"
    assert "Reindex failed" in (outcomes["poison"].reason or "")
    assert server.requests["poison"] == 6, "the fake must receive exactly 6 requests for the poison document"
    assert all(o.status == "OK" for d, o in outcomes.items() if d != "poison")
    assert all(server.requests[f"healthy-{i}"] == 1 for i in range(600))
