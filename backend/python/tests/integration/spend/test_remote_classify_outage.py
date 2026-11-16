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
from tests.support.stage_worker_harness import BudgetHarness, make_job

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

    h, job = BudgetHarness(fleet), make_job()
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
            assert elapsed / HOUR > 5.5, f"FAILED after {elapsed / HOUR:.2f} h of outage: {failed}"
            assert "20 times" in str(failed)
            return
    assert hours <= 5.5
    assert h.state(job).status is ProgressStatus.PAUSED
    state = await h.pause_state(job)
    assert state is not None and state.sends <= 20 and state.charged == 0


async def _job_key_across_the_wire(job_key: str) -> str:
    """The job key the Extraction gateway admits a call as: the real client builds the request inside the stage job,
    the route's request model parses it, and the route runs the call inside that job."""
    from app.api.routes.extraction import ClassifyRequest
    from app.services.llm_gateway.gateway import current_job_key, llm_job

    client = ExtractionClient(service_url="http://extraction:8093", max_retries=1)
    sent = AsyncMock(return_value=httpx.Response(200, json={"success": True, "classification": None}))
    with patch.object(client, "_post_json", new=sent), llm_job(job_key):
        await client.classify(BlocksContainer(blocks=[], block_groups=[]), "org-1")
    body = ClassifyRequest.model_validate(sent.call_args[0][1])
    with llm_job(body.job_key or ""):
        return current_job_key()


@pytest.fixture
def remote_admissions(monkeypatch) -> dict[str, str]:
    """Every admission of the simulation is made with the job key the Extraction service receives."""
    import tests.support.spend_sim as sim

    original = sim.FleetBreaker.admit
    crossed: dict[str, str] = {}

    async def admit(self, pk, cls, job=""):  # noqa: ANN202
        if job and job not in crossed:
            crossed[job] = await _job_key_across_the_wire(job)
        return await original(self, pk, cls, crossed.get(job, job))

    monkeypatch.setattr(sim.FleetBreaker, "admit", admit)
    return crossed


@pytest.mark.timeout(900)
@pytest.mark.parametrize("seed", [62011, 62017])
@pytest.mark.parametrize("kind", ["fast5xx", "hang"])
@pytest.mark.parametrize(("jobs", "hours", "max_sends"), [(20, 20, 11), (200, 20, 11), (20, 4, None), (200, 4, None)])
async def test_trials_rotate_over_the_jobs_in_remote_mode(fleet, remote_admissions, seed, kind, jobs, hours, max_sends) -> None:
    """A full outage, every call through the Extraction service: the half-open trials rotate over the waiting jobs, so
    no job is FAILED and none sends more than 11 times in 20 h. Without the job key on the wire one job carried most
    trials and ended at the absolute cap."""
    from tests.support.spend_scenarios import no_failure
    from tests.support.spend_sim import run_world

    res = await run_world(fleet, seed, jobs=jobs, outage_s=hours * HOUR, kind=kind)
    no_failure(max_sends=max_sends)(res)
    assert remote_admissions and all(job == crossed for job, crossed in remote_admissions.items())
