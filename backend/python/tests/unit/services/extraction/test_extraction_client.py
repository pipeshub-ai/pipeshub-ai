"""Tests for ExtractionClient HTTP client."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.config.constants.service import TokenScopes
from app.models.blocks import BlocksContainer, SemanticMetadata
from app.services.base_client import ServiceUnavailableError
from app.services.extraction.client import ExtractionClient, ExtractionClientError
from app.utils.llm import LLMNotConfiguredError, LLMUnavailableError


def _bc() -> BlocksContainer:
    return BlocksContainer(blocks=[], block_groups=[])


def _semantic_metadata_dict() -> dict:
    return {
        "departments": ["Engineering"],
        "languages": ["English"],
        "topics": ["Testing"],
        "summary": "A test document.",
        "categories": ["Technical"],
        "sub_category_level_1": "Software",
        "sub_category_level_2": "Python",
        "sub_category_level_3": "Testing",
    }


def _make_response(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_returns_semantic_metadata() -> None:
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)

    response_body = {
        "success": True,
        "classification": _semantic_metadata_dict(),
    }

    with patch.object(
        client,
        "_post_json",
        new=AsyncMock(return_value=_make_response(200, response_body)),
    ):
        result = await client.classify(_bc(), "org-123", departments=["Engineering"])

    assert isinstance(result, SemanticMetadata)
    assert "Engineering" in result.departments


@pytest.mark.asyncio
async def test_classify_returns_none_for_empty_document() -> None:
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)

    response_body = {"success": True, "classification": None}

    with patch.object(
        client,
        "_post_json",
        new=AsyncMock(return_value=_make_response(200, response_body)),
    ):
        result = await client.classify(_bc(), "org-123")

    assert result is None


@pytest.mark.asyncio
async def test_classify_passes_departments_in_request() -> None:
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)

    response_body = {"success": True, "classification": None}
    mock_post = AsyncMock(return_value=_make_response(200, response_body))

    with patch.object(client, "_post_json", new=mock_post):
        await client.classify(_bc(), "org-456", departments=["Finance", "HR"])

    call_args = mock_post.call_args
    payload = call_args[0][1]  # second positional arg = payload dict
    assert payload["departments"] == ["Finance", "HR"]
    assert payload["org_id"] == "org-456"
    assert payload["record_type"] == ""


@pytest.mark.asyncio
async def test_classify_forwards_record_type() -> None:
    """The service selects the code prompt from it; dropping it silently
    downgrades source files to the document prompt."""
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)

    mock_post = AsyncMock(
        return_value=_make_response(200, {"success": True, "classification": None})
    )

    with patch.object(client, "_post_json", new=mock_post):
        await client.classify(_bc(), "org-456", record_type="CODE_FILE")

    assert mock_post.call_args[0][1]["record_type"] == "CODE_FILE"


@pytest.mark.asyncio
async def test_classify_passes_record_name_and_type_in_request() -> None:
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)

    response_body = {"success": True, "classification": None}
    mock_post = AsyncMock(return_value=_make_response(200, response_body))

    with patch.object(client, "_post_json", new=mock_post):
        await client.classify(
            _bc(), "org-456", record_name="Q3 Board Deck.pdf", record_type="FILE",
        )

    call_args = mock_post.call_args
    payload = call_args[0][1]
    assert payload["record_name"] == "Q3 Board Deck.pdf"
    assert payload["record_type"] == "FILE"


@pytest.mark.asyncio
async def test_classify_sends_the_stage_job_it_runs_for() -> None:
    """The Extraction service's breaker rotates its half-open trials over the waiting jobs, so it must know which job a
    call is for; outside a stage job nothing is sent."""
    from app.services.llm_gateway.gateway import llm_job

    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)
    mock_post = AsyncMock(return_value=_make_response(200, {"success": True, "classification": None}))
    with patch.object(client, "_post_json", new=mock_post):
        with llm_job("classify:vr-1:rev-a"):
            await client.classify(_bc(), "org-1")
        await client.classify(_bc(), "org-1")
    assert mock_post.call_args_list[0][0][1]["job_key"] == "classify:vr-1:rev-a"
    assert "job_key" not in mock_post.call_args_list[1][0][1]


@pytest.mark.asyncio
async def test_classify_record_name_and_type_default_to_empty_string() -> None:
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)

    response_body = {"success": True, "classification": None}
    mock_post = AsyncMock(return_value=_make_response(200, response_body))

    with patch.object(client, "_post_json", new=mock_post):
        await client.classify(_bc(), "org-456")

    call_args = mock_post.call_args
    payload = call_args[0][1]
    assert payload["record_name"] == ""
    assert payload["record_type"] == ""


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_raises_extraction_client_error_on_service_failure() -> None:
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)

    response_body = {"success": False, "error": "LLM service unavailable"}

    with patch.object(
        client,
        "_post_json",
        new=AsyncMock(return_value=_make_response(500, response_body)),
    ):
        with pytest.raises(ExtractionClientError) as exc_info:
            await client.classify(_bc(), "org-123")

    assert "LLM service unavailable" in exc_info.value.message


@pytest.mark.asyncio
async def test_classify_raises_service_unavailable_on_connection_error() -> None:
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1, retry_delay=0.0)

    with patch.object(
        client,
        "_post_json",
        new=AsyncMock(side_effect=ServiceUnavailableError("Connection refused")),
    ):
        with pytest.raises(ServiceUnavailableError):
            await client.classify(_bc(), "org-123")


@pytest.mark.asyncio
async def test_classify_maps_llm_not_configured_to_typed_error() -> None:
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)
    body = {
        "success": False,
        "error": "No LLM is configured for this organization",
        "error_code": "LLM_NOT_CONFIGURED",
    }

    with patch.object(client, "_post_json", new=AsyncMock(return_value=_make_response(422, body))):
        with pytest.raises(LLMNotConfiguredError, match="No LLM is configured"):
            await client.classify(_bc(), "org-123")


@pytest.mark.asyncio
async def test_a_provider_outage_is_a_typed_error_that_costs_no_retry_or_breaker_failure() -> None:
    body = {"success": False, "error": "connection refused", "error_code": "LLM_UNAVAILABLE"}
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(424, json=body)

    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=3, retry_delay=0.0)
    client._auth_headers = AsyncMock(return_value={})  # type: ignore[method-assign]
    client._make_client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]

    with pytest.raises(LLMUnavailableError, match="connection refused"):
        await client.classify(_bc(), "org-123")
    assert calls == 1
    assert client.circuit_breaker._consecutive_failures == 0


_UNAVAILABLE = {"success": False, "error": "connection refused", "error_code": "LLM_UNAVAILABLE"}


@pytest.mark.asyncio
async def test_a_provider_outage_says_how_long_to_wait() -> None:
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)
    response = httpx.Response(424, json=_UNAVAILABLE, headers={"Retry-After": "30"})
    with patch.object(client, "_post_json", new=AsyncMock(return_value=response)), \
            pytest.raises(LLMUnavailableError) as raised:
        await client.classify(_bc(), "org-123")
    assert raised.value.retry_after == 30.0


@pytest.mark.asyncio
async def test_a_provider_outage_without_a_known_cooldown_names_no_wait() -> None:
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)
    response = httpx.Response(424, json=_UNAVAILABLE)
    with patch.object(client, "_post_json", new=AsyncMock(return_value=response)), \
            pytest.raises(LLMUnavailableError) as raised:
        await client.classify(_bc(), "org-123")
    assert raised.value.retry_after is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extra", "sent", "trial", "chargeable"),
    [
        ({"sent": False}, False, False, False),  # an open circuit behind the service: a refusal, not a send
        ({"sent": True, "trial": True}, True, True, False),
        ({"sent": True, "chargeable": True}, True, False, True),
        ({}, True, False, False),  # an older service names nothing: counted as a send, so no free sends
        ({"sent": None}, True, False, False),
    ],
    ids=["refused", "trial", "charged", "missing", "null"],
)
async def test_424_carries_sent(extra: dict, sent: bool, trial: bool, chargeable: bool) -> None:
    """The 424 body says whether a request reached the model provider; a body without it counts as a send."""
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)
    response = httpx.Response(424, json={**_UNAVAILABLE, **extra})
    with patch.object(client, "_post_json", new=AsyncMock(return_value=response)), \
            pytest.raises(LLMUnavailableError) as raised:
        await client.classify(_bc(), "org-123")
    assert (raised.value.sent, raised.value.trial, raised.value.chargeable) == (sent, trial, chargeable)


# ---------------------------------------------------------------------------
# Service token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_passes_org_for_token() -> None:
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)
    mock_post = AsyncMock(return_value=_make_response(200, {"success": True, "classification": None}))

    with patch.object(client, "_post_json", new=mock_post):
        await client.classify(_bc(), "org-123")

    assert mock_post.await_args.kwargs["org_id"] == "org-123"


def test_extraction_client_uses_document_classify_scope() -> None:
    config_service = MagicMock()
    client = ExtractionClient(service_url="http://fake-extraction:8093", config_service=config_service)

    assert client._service_scope is TokenScopes.DOCUMENT_CLASSIFY
    assert client._config_service is config_service


def _extraction_body() -> dict:
    return {
        "status": "COMPLETED",
        "termination_reason": "finish_ok",
        "strategy": "agent",
        "entities": [
            {"kind": "organization", "display_name": "Acme", "norm_key": "org:acme"},
            {"kind": "url", "display_name": "https://acme.example", "norm_key": "url:https://acme.example"},
        ],
        "stats": {"units": 1},
    }


async def _extract(body: dict):
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)
    with patch.object(
        client,
        "_post_json",
        new=AsyncMock(return_value=_make_response(200, {"success": True, "extraction": body})),
    ):
        return await client.extract_entities(_bc(), "org-123")


@pytest.mark.asyncio
async def test_extract_entities_reads_a_newer_service_reply() -> None:
    body = _extraction_body()
    body["future_top"] = 1
    body["stats"]["future_stat"] = 2
    body["entities"][0]["future_entity"] = 3

    result = await _extract(body)

    assert result.status == "COMPLETED"
    assert [entity.display_name for entity in result.entities] == ["Acme", "https://acme.example"]


@pytest.mark.asyncio
async def test_extract_entities_drops_only_the_entity_of_an_unknown_kind() -> None:
    body = _extraction_body()
    body["entities"][0]["kind"] = "vehicle"

    result = await _extract(body)

    assert [entity.display_name for entity in result.entities] == ["https://acme.example"]
    assert result.status == "PARTIAL"


@pytest.mark.asyncio
async def test_extract_entities_without_an_extraction_is_failed() -> None:
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)
    with patch.object(
        client,
        "_post_json",
        new=AsyncMock(return_value=_make_response(200, {"success": True, "extraction": None})),
    ):
        result = await client.extract_entities(_bc(), "org-123")

    assert (result.status, result.termination_reason) == ("FAILED", "llm_error")


@pytest.mark.asyncio
async def test_a_busy_extraction_service_is_waited_out_without_tripping_the_breaker() -> None:
    replies = [
        httpx.Response(429, headers={"Retry-After": "0"}, json={"success": False, "error": "busy"}),
        httpx.Response(200, json={"success": True, "extraction": _extraction_body()}),
    ]
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)
    client._auth_headers = AsyncMock(return_value={})  # type: ignore[method-assign]
    client._make_client = lambda: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=httpx.MockTransport(lambda request: replies.pop(0)),
    )
    with patch.object(client.circuit_breaker, "record_failure") as failure:
        result = await client.extract_entities(_bc(), "org-123")

    assert result.status == "COMPLETED"
    failure.assert_not_called()


# ---------------------------------------------------------------------------
# What the service spends for the caller's job reaches the job's pause budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_job_sends_its_remaining_allowance_and_counts_what_the_service_spent() -> None:
    """The service is told how many failed sends the job has left, and the job adds what the service reports it spent,
    on a served call as on an outage."""
    from app.services.llm_gateway.gateway import llm_job

    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)
    usage = {"failed": 2, "charged": 1, "trials": 0, "unavailable": 0, "pk": "pk-1", "size_class": "s", "provider": "P"}
    served = _make_response(200, {"success": True, "classification": None, "usage": usage})
    mock_post = AsyncMock(return_value=served)
    with patch.object(client, "_post_json", new=mock_post), llm_job("classify:vr-1:rev-a", allowance=5) as trace:
        await client.classify(_bc(), "org-1")
    assert mock_post.call_args[0][1]["send_allowance"] == 5
    assert (trace.requests, trace.charged) == (2, 1)
    assert trace.meter.remaining() == 3


@pytest.mark.asyncio
async def test_an_entities_outage_behind_the_service_is_a_provider_outage_the_job_counts() -> None:
    """``POST /entities`` answers a provider outage with the 424 classify uses: the client raises the typed error the
    stage pauses on, and the job counts the sends the service made."""
    from app.services.llm_gateway.gateway import llm_job

    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)
    # The service's own trace saw no outage (its extractor raised a plain error): the 424 itself is one.
    usage = {"failed": 3, "charged": 2, "trials": 0, "unavailable": 0, "pk": "pk-1", "size_class": "l", "provider": "P"}
    body = {**_UNAVAILABLE, "sent": True, "requests": 3, "usage": usage}
    with patch.object(client, "_post_json", new=AsyncMock(return_value=httpx.Response(424, json=body))), llm_job(
        "entities:vr-1:rev-a", allowance=10
    ) as trace, pytest.raises(LLMUnavailableError):
        await client.extract_entities(_bc(), "org-1")
    fields = trace.pause_fields()
    assert (fields["requests"], fields["charged"], fields["provider_pk"]) == (3, 2, "pk-1")


@pytest.mark.asyncio
async def test_an_outage_from_a_service_without_usage_is_counted_from_its_424() -> None:
    from app.services.llm_gateway.gateway import llm_job

    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)
    body = {**_UNAVAILABLE, "sent": True, "requests": 2, "chargeable": True, "pk": "pk-1", "size_class": "s"}
    with patch.object(client, "_post_json", new=AsyncMock(return_value=httpx.Response(424, json=body))), llm_job(
        "classify:vr-1:rev-a"
    ) as trace, pytest.raises(LLMUnavailableError):
        await client.classify(_bc(), "org-1")
    assert (trace.requests, trace.charged, trace.unavailable) == (2, 1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(("recorded", "charged_sends"), [({"failed": 2, "charged": 1}, 2), (None, 7)])
async def test_a_lost_reply_is_charged_what_the_service_recorded_or_everything_it_allowed(
    recorded: dict | None, charged_sends: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The request may have been received and its reply lost: the job is charged what the service recorded under the
    request id, or, when nothing is recorded (still running, or it died), the whole allowance it was given."""
    from app.services.llm_gateway import remote_usage
    from app.services.llm_gateway.gateway import llm_job

    asked: list[tuple[str, str]] = []

    async def read(job_key: str, request_id: str) -> dict | None:
        asked.append((job_key, request_id))
        return recorded

    monkeypatch.setattr(remote_usage, "read", read)
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)
    lost = AsyncMock(side_effect=ServiceUnavailableError("read timeout", delivered=True))
    with patch.object(client, "_post_json", new=lost), llm_job("entities:vr-1:rev-a", allowance=7) as trace:
        with pytest.raises(ServiceUnavailableError):
            await client.extract_entities(_bc(), "org-1")
    sent = lost.call_args[0][1]
    assert asked == [("entities:vr-1:rev-a", sent["request_id"])]
    assert trace.requests == charged_sends and trace.unavailable == 1


@pytest.mark.asyncio
async def test_a_replayed_request_is_retried_by_the_stage_and_charged_the_first_deliverys_usage() -> None:
    """A duplicate the service refuses is a redelivery, not a lost reply: charged what the first spent, and handed to
    the stage's retry path (which counts the attempt), not its pause schedule."""
    from app.services.llm_gateway.gateway import llm_job

    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)
    body = {"success": False, "error_code": "REPLAYED", "usage": {"failed": 3, "charged": 0}}
    replayed = AsyncMock(return_value=httpx.Response(409, json=body))
    with patch.object(client, "_post_json", new=replayed), llm_job("classify:vr-1:rev-a", allowance=9) as trace:
        with pytest.raises(ExtractionClientError):
            await client.classify(_bc(), "org-1")
    assert trace.requests == 3


@pytest.mark.asyncio
async def test_a_paid_request_is_never_sent_twice_once_it_may_have_been_received() -> None:
    """A 500 the service answered is handed back after one request; the client does not resend the same payload,
    which the service would only refuse as a duplicate."""
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=3)
    sent = 0

    def answer(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        return httpx.Response(500, json={"success": False, "error": "boom"})

    async def no_auth(_org_id: str | None) -> dict[str, str]:
        return {}

    transport = httpx.MockTransport(answer)
    with patch.object(client, "_make_client", new=lambda: httpx.AsyncClient(transport=transport)), patch.object(
        client, "_auth_headers", new=no_auth
    ):
        with pytest.raises(ExtractionClientError):
            await client.classify(_bc(), "org-1")
    assert sent == 1


@pytest.mark.asyncio
async def test_an_outage_body_without_usage_keeps_its_charged_count() -> None:
    from app.services.llm_gateway.gateway import llm_job

    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)
    body = {**_UNAVAILABLE, "sent": True, "requests": 3, "charged": 3, "chargeable": True, "pk": "pk-1", "size_class": "s"}
    with patch.object(client, "_post_json", new=AsyncMock(return_value=httpx.Response(424, json=body))), llm_job(
        "classify:vr-1:rev-a"
    ) as trace, pytest.raises(LLMUnavailableError):
        await client.classify(_bc(), "org-1")
    assert trace.charged == 3


@pytest.mark.asyncio
async def test_a_request_that_never_reached_the_service_is_not_charged() -> None:
    from app.services.llm_gateway.gateway import llm_job

    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)
    refused = AsyncMock(side_effect=ServiceUnavailableError("connect refused", delivered=False))
    with patch.object(client, "_post_json", new=refused), llm_job("classify:vr-1:rev-a", allowance=9) as trace:
        with pytest.raises(ServiceUnavailableError):
            await client.classify(_bc(), "org-1")
    assert trace.requests == 0


@pytest.mark.asyncio
async def test_a_proxy_5xx_with_no_account_of_the_work_is_charged_and_pauses() -> None:
    """A 502 from a proxy in front of the service says nothing of what was spent: the job is charged what the service
    recorded (else everything it allowed), and the call is an unavailable service, so the stage pauses."""
    from app.services.llm_gateway.gateway import llm_job

    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)
    proxied = AsyncMock(return_value=httpx.Response(502, text="Bad Gateway"))
    with patch.object(client, "_post_json", new=proxied), llm_job("classify:vr-1:rev-a", allowance=4) as trace:
        with pytest.raises(ServiceUnavailableError) as raised:
            await client.classify(_bc(), "org-1")
    assert raised.value.delivered is True and trace.requests == 4


@pytest.mark.asyncio
async def test_the_services_own_500_with_usage_is_a_retry_charged_what_it_spent() -> None:
    from app.services.llm_gateway.gateway import llm_job

    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)
    answered = AsyncMock(return_value=httpx.Response(500, json={"success": False, "error": "boom", "usage": {"failed": 1}}))
    with patch.object(client, "_post_json", new=answered), llm_job("classify:vr-1:rev-a", allowance=4) as trace:
        with pytest.raises(ExtractionClientError):
            await client.classify(_bc(), "org-1")
    assert trace.requests == 1
