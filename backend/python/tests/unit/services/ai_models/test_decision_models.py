"""Decision model clients: wire formats, parsing, retries and errors for each provider."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import httpx
import pytest
from pydantic import ValidationError

from app.services.ai_models.decision import (
    AzureOpenAIDecisionModel,
    ChoiceQuestion,
    DecisionModelError,
    DecisionOption,
    OpenAIDecisionModel,
    PredicateQuestion,
    ScoreLevel,
    ScoreQuestion,
    TypeSafeDecisionModel,
    get_decision_model,
)
from app.utils.user_errors import provider_error_code

if TYPE_CHECKING:
    from collections.abc import Iterator
    from contextlib import AbstractContextManager

QUESTIONS = [
    PredicateQuestion(name="urgent", instructions="Is this urgent?"),
    ChoiceQuestion(
        name="team",
        instructions="Which team should handle this?",
        options=[
            DecisionOption(value="payments", description="Payments and refunds"),
            DecisionOption(value="technical"),
        ],
    ),
    ScoreQuestion(
        name="severity",
        instructions="How severe is it?",
        levels=[ScoreLevel(label="Low"), ScoreLevel(label="High", description="Fully blocked")],
    ),
]


def _mock(responses: list[httpx.Response], seen: list[httpx.Request]) -> AbstractContextManager[object]:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses.pop(0)

    def factory(_url: str | None, *, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)

    return patch("app.services.ai_models.decision.clients.guarded_async_client", factory)


@pytest.fixture(autouse=True)
def _no_sleep() -> Iterator[None]:
    with patch("app.services.ai_models.decision.clients.asyncio.sleep", return_value=None):
        yield


OPENAI_RESPONSE = {
    "answers": [
        {"type": "predicate", "name": "urgent", "probability": 0.8},
        {
            "type": "choice",
            "name": "team",
            "choice": "payments",
            "probabilities": [
                {"value": "payments", "probability": 0.9},
                {"value": "technical", "probability": 0.1},
            ],
            "confidence": 0.87,
        },
        {
            "type": "score",
            "name": "severity",
            "score": 0.3,
            "probabilities": [
                {"value": 0, "label": "Low", "probability": 0.7},
                {"value": 1, "label": "High", "probability": 0.3},
            ],
            "confidence": 0.4,
        },
    ],
}


async def test_openai_sends_the_decisions_format_and_parses_answers() -> None:
    seen: list[httpx.Request] = []
    model = OpenAIDecisionModel("gpt-6-luna", api_key="sk-test", organization="org-1")
    with _mock([httpx.Response(200, json=OPENAI_RESPONSE)], seen):
        result = await model.decide({"text": "charged twice"}, QUESTIONS)

    request = seen[0]
    assert str(request.url) == "https://api.openai.com/v1/decisions"
    assert request.headers["authorization"] == "Bearer sk-test"
    assert request.headers["openai-organization"] == "org-1"
    body = json.loads(request.content)
    assert body["model"] == "gpt-6-luna"
    assert body["input"] == '{"text": "charged twice"}'
    assert body["questions"][0] == {"type": "predicate", "name": "urgent", "instructions": "Is this urgent?"}
    assert body["questions"][1]["choices"][1] == {"value": "technical", "description": "technical"}
    assert body["questions"][2]["levels"][1] == {"label": "High", "description": "Fully blocked"}

    assert result.answers["urgent"].probability == 0.8
    assert result.answers["team"].choice == "payments"
    assert result.answers["team"].probabilities == {"payments": 0.9, "technical": 0.1}
    assert result.answers["severity"].score == 0.3
    assert result.answers["severity"].probabilities == {"Low": 0.7, "High": 0.3}


async def test_openai_refusal_and_missing_answers_are_refused() -> None:
    seen: list[httpx.Request] = []
    payload = {"answers": [{"type": "refusal", "name": "urgent"}]}
    model = OpenAIDecisionModel("gpt-6-luna", api_key="sk-test")
    with _mock([httpx.Response(200, json=payload)], seen):
        result = await model.decide("text", QUESTIONS)
    assert result.answers["urgent"].refused
    assert not result.answers["urgent"].missing
    assert result.answers["team"].missing and result.answers["severity"].missing
    assert result.answers["team"].choice is None


async def test_openai_custom_base_url() -> None:
    seen: list[httpx.Request] = []
    model = OpenAIDecisionModel("gpt-6-luna", api_key="k", base_url="https://gateway.example.com/v1/")
    with _mock([httpx.Response(200, json={"answers": []})], seen):
        await model.decide("x", QUESTIONS[:1])
    assert str(seen[0].url) == "https://gateway.example.com/v1/decisions"


async def test_azure_calls_the_foundry_system_one_path_by_deployment_name() -> None:
    seen: list[httpx.Request] = []
    model = get_decision_model(
        "azureOpenAI",
        {
            "configuration": {
                "model": "Microsoft-Decision-1",
                "deploymentName": "decision-prod",
                "apiKey": "az-key",
                "endpoint": "https://res.openai.azure.com/openai/v1/",
            },
            "isDefault": True,
        },
    )
    assert isinstance(model, AzureOpenAIDecisionModel)
    payload = {"model": "microsoft-decision-1", "answers": {"urgent": {"type": "noul", "noul": 0.7}}}
    with _mock([httpx.Response(200, json=payload)], seen):
        result = await model.decide("x", QUESTIONS[:1])
    assert str(seen[0].url) == "https://res.openai.azure.com/providers/microsoft/v1/systemone"
    assert seen[0].headers["api-key"] == "az-key"
    assert "authorization" not in seen[0].headers
    body = json.loads(seen[0].content)
    assert body["model"] == "decision-prod"
    assert body["questions"]["urgent"] == {"type": "noul", "instructions": "Is this urgent?"}
    assert result.answers["urgent"].probability == 0.7


async def test_typesafe_sends_system_one_format_and_parses_answers() -> None:
    seen: list[httpx.Request] = []
    payload = {
        "model": "jev-1.13.0",
        "answers": {
            "urgent": {"type": "noul", "noul": 0.9},
            "team": {"type": "choice", "choice": "technical", "probabilities": {"payments": 0.2, "technical": 0.8}, "confidence": 0.6},
            "severity": {"type": "score", "score": 1.0, "legend": {"0": "Low", "1": "High"}, "probabilities": {"0": 0.0, "1": 1.0}, "confidence": 1.0},
        },
        "usage": {"input_tokens": 120, "output_tokens": 9},
    }
    model = TypeSafeDecisionModel("jev-latest", api_key="ts-key")
    with _mock([httpx.Response(200, json=payload)], seen):
        result = await model.decide({"ticket": "payouts failing"}, QUESTIONS)

    request = seen[0]
    assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
    assert request.headers["authorization"] == "Bearer ts-key"
    body = json.loads(request.content)
    assert body["state"] == {"ticket": "payouts failing"}
    assert body["questions"]["urgent"] == {"type": "noul", "instructions": "Is this urgent?"}
    assert body["questions"]["team"]["criteria"] == {"payments": "Payments and refunds", "technical": None}
    assert body["questions"]["severity"]["criteria"] == ["Low", "High: Fully blocked"]

    assert result.model == "jev-1.13.0"
    assert result.answers["urgent"].probability == 0.9
    assert result.answers["urgent"].confidence == pytest.approx(0.8)
    assert result.answers["team"].choice == "technical"
    assert result.answers["severity"].probabilities == {"Low": 0.0, "High": 1.0}
    assert result.usage.input_tokens == 120


async def test_retries_rate_limit_then_succeeds() -> None:
    seen: list[httpx.Request] = []
    model = TypeSafeDecisionModel("jev-latest", api_key="k")
    responses = [
        httpx.Response(429, headers={"retry-after": "1"}, json={"error": "slow down"}),
        httpx.Response(529, json={"error": "overloaded"}),
        httpx.Response(200, json={"answers": {"urgent": {"type": "noul", "noul": 0.1}}}),
    ]
    with _mock(responses, seen):
        result = await model.decide("x", QUESTIONS[:1])
    assert len(seen) == 3
    assert result.answers["urgent"].probability == 0.1


async def test_auth_failure_raises_with_status_the_health_check_can_classify() -> None:
    seen: list[httpx.Request] = []
    model = OpenAIDecisionModel("gpt-6-luna", api_key="bad")
    error = {"error": {"message": "Incorrect API key provided", "code": "invalid_api_key"}}
    with _mock([httpx.Response(401, json=error)], seen), pytest.raises(DecisionModelError) as caught:
        await model.decide("x", QUESTIONS[:1])
    assert caught.value.status_code == 401
    assert "Incorrect API key" not in str(caught.value)
    assert provider_error_code(caught.value) == "auth_error"
    assert len(seen) == 1


async def test_long_retry_after_is_not_waited_for() -> None:
    seen: list[httpx.Request] = []
    model = TypeSafeDecisionModel("jev-latest", api_key="k")
    with _mock([httpx.Response(429, headers={"retry-after": "30"}, json={})], seen), pytest.raises(
        DecisionModelError
    ) as caught:
        await model.decide("x", QUESTIONS[:1])
    assert caught.value.status_code == 429
    assert len(seen) == 1
    assert provider_error_code(caught.value) == "rate_limit"


async def test_single_attempt_does_not_retry() -> None:
    seen: list[httpx.Request] = []
    model = TypeSafeDecisionModel("jev-latest", api_key="k")
    with _mock([httpx.Response(503, json={})], seen), pytest.raises(DecisionModelError):
        await model.decide("x", QUESTIONS[:1], max_attempts=1)
    assert len(seen) == 1


async def test_rejects_empty_and_duplicate_questions() -> None:
    model = TypeSafeDecisionModel("jev-latest", api_key="k")
    with pytest.raises(ValueError):
        await model.decide("x", [])
    with pytest.raises(ValueError):
        await model.decide("x", [QUESTIONS[0], QUESTIONS[0]])


def test_question_validation() -> None:
    with pytest.raises(ValidationError):
        ChoiceQuestion(name="c", instructions="?", options=[DecisionOption(value="a")])
    with pytest.raises(ValidationError):
        ChoiceQuestion(name="c", instructions="?", options=[DecisionOption(value="a"), DecisionOption(value="a")])
    with pytest.raises(ValidationError):
        ScoreQuestion(name="s", instructions="?", levels=[ScoreLevel(label=str(i)) for i in range(11)])


def test_factory_builds_each_provider_and_rejects_unknown() -> None:
    entry = {"configuration": {"model": "jev-latest", "apiKey": "k"}, "isDefault": True}
    assert isinstance(get_decision_model("typesafe", entry), TypeSafeDecisionModel)
    assert isinstance(get_decision_model("openAI", {**entry, "configuration": {"model": "gpt-6-luna", "apiKey": "k"}}), OpenAIDecisionModel)
    with pytest.raises(ValueError, match="Unsupported decision model provider"):
        get_decision_model("anthropic", entry)
    with pytest.raises(ValueError, match="endpoint"):
        get_decision_model("azureOpenAI", entry)
    with pytest.raises(ValueError, match="No decision model configured"):
        get_decision_model("typesafe", {"configuration": {"apiKey": "k"}})


def test_factory_refuses_metadata_endpoint() -> None:
    entry = {"configuration": {"model": "jev-latest", "apiKey": "k", "endpoint": "http://169.254.169.254"}}
    with pytest.raises(ValueError, match="never allowed"):
        get_decision_model("typesafe", entry)


async def test_system_one_reader_accepts_probability_field_and_list_probabilities() -> None:
    seen: list[httpx.Request] = []
    payload = {
        "answers": {
            "urgent": {"type": "noul", "probability": 0.25},
            "team": {
                "type": "choice",
                "choice": "payments",
                "probabilities": [{"value": "payments", "probability": 0.7}, {"value": "technical", "probability": 0.3}],
            },
            "severity": {"type": "score", "score": 0.4, "probabilities": [{"value": 0, "probability": 0.6}, {"value": 1, "probability": 0.4}]},
        }
    }
    model = TypeSafeDecisionModel("jev-latest", api_key="k")
    with _mock([httpx.Response(200, json=payload)], seen):
        result = await model.decide("x", QUESTIONS)
    assert result.answers["urgent"].probability == 0.25
    assert result.answers["team"].probabilities == {"payments": 0.7, "technical": 0.3}
    assert result.answers["severity"].probabilities == {"Low": 0.6, "High": 0.4}
    assert not any(answer.missing for answer in result.answers.values())


def test_azure_endpoint_is_reduced_to_the_resource_root() -> None:
    for endpoint in (
        "https://res.openai.azure.com/",
        "https://res.openai.azure.com/openai/v1/",
        "https://res.services.ai.azure.com/api/projects/my-project",
        "https://res.services.ai.azure.com/api/projects/my-project/openai/v1",
    ):
        model = AzureOpenAIDecisionModel("decision-prod", api_key="k", endpoint=endpoint)
        host = endpoint.split("/")[2]
        assert model._url() == f"https://{host}/providers/microsoft/v1/systemone", endpoint


async def test_a_yes_no_probability_outside_0_to_1_counts_as_missing() -> None:
    seen: list[httpx.Request] = []
    model = TypeSafeDecisionModel("jev-latest", api_key="k")
    with _mock([httpx.Response(200, json={"answers": {"urgent": {"type": "noul", "noul": 1.7}}})], seen):
        result = await model.decide("x", QUESTIONS[:1])
    answer = result.answers["urgent"]
    assert answer.missing and answer.probability is None and answer.confidence is None
