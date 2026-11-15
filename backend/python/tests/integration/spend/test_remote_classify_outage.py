"""Classify through the Extraction service (``USE_PARSING_SERVICE=true``) in an outage.

The Extraction process binds the same fleet Redis, so its gateway shares the breaker state; its 424 body carries the provider key digest,
size class and label, so the indexing side can charge, hold and read the key's ledger. The pieces are real: the gateway, the route's
response model, the client's rebuild of the error, the stage result and the worker's pause budget.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.api.routes.extraction import UnavailableResponse
from app.models.blocks import BlocksContainer
from app.modules.pipeline.models import ProgressStatus, StageResult
from app.modules.pipeline.worker import StageFailed, StagePaused
from app.services.extraction.client import ExtractionClient
from app.services.llm_gateway.gateway import LLMGateway, ProviderUnavailableError
from app.utils.llm import LLMUnavailableError
from tests.unit.modules.pipeline.test_worker import _BudgetHarness, _job

pytestmark = pytest.mark.timeout(300)
HOUR = 3600.0


async def _through_the_424(exc: ProviderUnavailableError) -> LLMUnavailableError:
    """What the indexing side sees: the route's body, parsed by the client."""
    body = UnavailableResponse(
        success=False, error=str(exc), error_code=LLMUnavailableError.code, sent=exc.sent, requests=exc.requests, pk=exc.pk,
        size_class=exc.size_class, provider=exc.provider, trial=exc.trial, chargeable=exc.chargeable,
    ).model_dump()
    client = ExtractionClient(service_url="http://extraction:8093", max_retries=1)
    response = httpx.Response(424, json=body)
    with patch.object(client, "_post_json", new=AsyncMock(return_value=response)), pytest.raises(LLMUnavailableError) as raised:
        await client.classify(BlocksContainer(blocks=[], block_groups=[]), "org-1")
    return raised.value


@pytest.mark.parametrize("hours", [4.0, 20.0])
async def test_a_lone_job_survives_an_outage_in_remote_mode(fleet, hours: float) -> None:
    """Expected: 0 FAILED for 1 h and 4 h; a lone job survives 4 h (13 to 15 sends) and, at 20 h, ends only at the absolute cap."""
    service_gateway = LLMGateway(4)  # one long-lived gateway in the Extraction process

    async def down() -> None:
        raise ConnectionError("provider down")

    h, job = _BudgetHarness(fleet), _job()
    h.seed(job)
    elapsed = 0.0
    while elapsed < hours * HOUR:
        try:
            await service_gateway.call(down, provider="ChatX:m@host")
        except ProviderUnavailableError as exc:
            remote = await _through_the_424(exc)
        assert remote.pk and remote.size_class and remote.provider == "ChatX:m@host"
        h.stage.result = StageResult.paused_for(remote, f"Model provider unavailable: {remote}")
        try:
            await h.deliver(job)
        except StagePaused as paused:
            elapsed += paused.delay_s
            h.clock.advance(int(paused.delay_s * 1000))
        except StageFailed as failed:
            assert hours > 5.5, f"FAILED after {elapsed / HOUR:.2f} h of outage: {failed}"
            assert "20 times" in str(failed)
            return
    assert hours <= 5.5
    assert h.state(job).status is ProgressStatus.PAUSED
    state = await h.pause_state(job)
    assert state is not None and state.sends <= 20 and state.charged == 0
