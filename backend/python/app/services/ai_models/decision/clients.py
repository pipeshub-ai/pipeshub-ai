"""Decision model clients: OpenAI Decisions API, TypeSafe (Jev) and Microsoft Foundry.

Each client turns the shared question types into its provider's wire format and
the provider's answers back into ``DecisionResult``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx

from app.services.ai_models.decision.types import (
    ChoiceQuestion,
    DecisionAnswer,
    DecisionInput,
    DecisionModelError,
    DecisionQuestion,
    DecisionResult,
    DecisionUsage,
    PredicateQuestion,
    ScoreQuestion,
)
from app.services.base_client import parse_retry_after
from app.utils.model_egress import guarded_async_client

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

OPENAI_BASE_URL = "https://api.openai.com/v1"
TYPESAFE_BASE_URL = "https://api.typesafe.ai"

_REQUEST_TIMEOUT_S = 30.0
# 529 is TypeSafe's "temporarily overloaded".
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504, 529})
MAX_ATTEMPTS = 3
_BACKOFF_S = 0.5
# A longer Retry-After than this is not worth waiting for inside a request.
_MAX_RETRY_AFTER_S = 5.0


def _as_text(value: DecisionInput) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _probability(value: object) -> float | None:
    number = _float(value)
    return number if number is not None and 0.0 <= number <= 1.0 else None


def _error_message(response: httpx.Response) -> str:
    if response.status_code in (401, 403):
        # Auth errors can echo part of the key; the status says enough.
        return "the provider refused the credentials"
    try:
        body = response.json()
    except ValueError:
        return response.text[:300] or f"HTTP {response.status_code}"
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or body)[:300]
    if isinstance(body, dict):
        return str(body.get("message") or body.get("detail") or error or body)[:300]
    return str(body)[:300]


def _retry_delay(response: httpx.Response, attempt: int) -> float | None:
    """Seconds to wait before the next attempt, or None when the provider asks for longer."""
    retry_after = parse_retry_after(response.headers.get("retry-after"))
    if retry_after is None:
        return _BACKOFF_S * (2 ** attempt)
    return retry_after if retry_after <= _MAX_RETRY_AFTER_S else None


class DecisionModel(ABC):
    """Answers typed questions about an input.

    ``decide`` sends every question in one request. Questions that depend on an
    earlier answer need a separate call.
    """

    provider: str

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    async def decide(
        self,
        content: DecisionInput,
        questions: Sequence[DecisionQuestion],
        *,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> DecisionResult:
        if not questions:
            raise ValueError("A decision request needs at least one question")
        names = [question.name for question in questions]
        if len(set(names)) != len(names):
            raise ValueError("Question names must be unique within a request")
        payload = await self._post(self._request_body(content, questions), max_attempts)
        result = self._parse(payload, questions)
        if all(answer.missing for answer in result.answers.values()):
            logger.warning(
                "%s decision response had no answer we could read; check the endpoint serves this API",
                self.provider,
            )
        return result

    @abstractmethod
    def _url(self) -> str: ...

    @abstractmethod
    def _headers(self) -> dict[str, str]: ...

    @abstractmethod
    def _request_body(self, content: DecisionInput, questions: Sequence[DecisionQuestion]) -> dict[str, Any]: ...

    @abstractmethod
    def _parse(self, payload: dict[str, Any], questions: Sequence[DecisionQuestion]) -> DecisionResult: ...

    async def _post(self, body: dict[str, Any], max_attempts: int) -> dict[str, Any]:
        url = self._url()
        headers = {"Content-Type": "application/json", **self._headers()}
        async with guarded_async_client(url, timeout=_REQUEST_TIMEOUT_S) as client:
            for attempt in range(max_attempts):
                try:
                    response = await client.post(url, json=body, headers=headers)
                except httpx.TimeoutException as exc:
                    raise DecisionModelError(f"{self.provider} decision request timed out") from exc
                except httpx.HTTPError as exc:
                    raise DecisionModelError(f"{self.provider} decision request failed: {exc}") from exc
                if response.status_code in _RETRY_STATUSES and attempt < max_attempts - 1:
                    delay = _retry_delay(response, attempt)
                    if delay is not None:
                        await asyncio.sleep(delay)
                        continue
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise DecisionModelError(
                        f"{self.provider} decision request returned HTTP {response.status_code}: "
                        f"{_error_message(response)}",
                        status_code=response.status_code,
                    ) from exc
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise DecisionModelError(f"{self.provider} returned a non-JSON decision response") from exc
                if not isinstance(payload, dict):
                    raise DecisionModelError(f"{self.provider} returned an unexpected decision response")
                return payload
        raise DecisionModelError(f"{self.provider} decision request failed")  # pragma: no cover


class OpenAIDecisionModel(DecisionModel):
    """OpenAI ``POST /v1/decisions``."""

    provider = "openAI"

    def __init__(
        self,
        model_name: str,
        api_key: str,
        base_url: str | None = None,
        organization: str | None = None,
    ) -> None:
        super().__init__(model_name)
        self._api_key = api_key
        self._base_url = (base_url or OPENAI_BASE_URL).rstrip("/")
        self._organization = organization

    def _url(self) -> str:
        return f"{self._base_url}/decisions"

    def _headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if self._organization:
            headers["OpenAI-Organization"] = self._organization
        return headers

    def _request_body(self, content: DecisionInput, questions: Sequence[DecisionQuestion]) -> dict[str, Any]:
        return {
            "model": self.model_name,
            "input": _as_text(content),
            "questions": [_openai_question(question) for question in questions],
        }

    def _parse(self, payload: dict[str, Any], questions: Sequence[DecisionQuestion]) -> DecisionResult:
        by_name = {question.name: question for question in questions}
        answers: dict[str, DecisionAnswer] = {}
        for row in payload.get("answers") or []:
            if not isinstance(row, dict) or row.get("name") not in by_name:
                continue
            answers[row["name"]] = _openai_answer(row, by_name[row["name"]])
        return DecisionResult(
            model=str(payload.get("model") or self.model_name),
            answers=_fill_missing(answers, questions),
            usage=_usage(payload.get("usage")),
        )


class TypeSafeDecisionModel(DecisionModel):
    """TypeSafe System One models (Jev): ``POST /v1/systemone``."""

    provider = "typesafe"

    def __init__(self, model_name: str, api_key: str, base_url: str | None = None) -> None:
        super().__init__(model_name)
        self._api_key = api_key
        self._base_url = (base_url or TYPESAFE_BASE_URL).rstrip("/")

    def _url(self) -> str:
        return f"{self._base_url}/v1/systemone"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _request_body(self, content: DecisionInput, questions: Sequence[DecisionQuestion]) -> dict[str, Any]:
        return {
            "state": content,
            "model": self.model_name,
            "questions": {question.name: _typesafe_question(question) for question in questions},
        }

    def _parse(self, payload: dict[str, Any], questions: Sequence[DecisionQuestion]) -> DecisionResult:
        raw_answers = payload.get("answers") if isinstance(payload.get("answers"), dict) else {}
        answers: dict[str, DecisionAnswer] = {}
        for question in questions:
            row = raw_answers.get(question.name)
            if isinstance(row, dict):
                answers[question.name] = _typesafe_answer(row, question)
        return DecisionResult(
            model=str(payload.get("model") or self.model_name),
            answers=_fill_missing(answers, questions),
            usage=_usage(payload.get("usage")),
        )


class AzureOpenAIDecisionModel(TypeSafeDecisionModel):
    """A Microsoft Foundry deployment of a decision model (Microsoft-Decision-1).

    Foundry serves it in the System One format on the resource root, addressed by
    deployment name: ``POST {endpoint}/providers/microsoft/v1/systemone``.
    """

    provider = "azureOpenAI"

    def __init__(self, model_name: str, api_key: str, endpoint: str) -> None:
        super().__init__(model_name, api_key, base_url=_azure_resource_root(endpoint))

    def _url(self) -> str:
        return f"{self._base_url}/providers/microsoft/v1/systemone"

    def _headers(self) -> dict[str, str]:
        return {"api-key": self._api_key}


def _azure_resource_root(endpoint: str) -> str:
    # The decision route is on the resource root; admins often paste a longer URL
    # (".../openai/v1/", or a Foundry project's ".../api/projects/<name>").
    parts = urlsplit(endpoint.strip())
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else endpoint.strip().rstrip("/")


def _openai_question(question: DecisionQuestion) -> dict[str, Any]:
    body: dict[str, Any] = {"type": question.type, "name": question.name, "instructions": question.instructions}
    if isinstance(question, ChoiceQuestion):
        body["choices"] = [
            {"value": option.value, "description": option.description or option.value}
            for option in question.options
        ]
    elif isinstance(question, ScoreQuestion):
        body["levels"] = [
            {"label": level.label, "description": level.description or level.label}
            for level in question.levels
        ]
    return body


def _openai_answer(row: dict[str, Any], question: DecisionQuestion) -> DecisionAnswer:
    if row.get("type") == "refusal":
        return DecisionAnswer(name=question.name, type=question.type, refused=True)
    answer = DecisionAnswer(name=question.name, type=question.type, confidence=_float(row.get("confidence")))
    probabilities = row.get("probabilities") if isinstance(row.get("probabilities"), list) else []
    if isinstance(question, PredicateQuestion):
        answer.probability = _probability(row.get("probability"))
        answer.missing = answer.probability is None
    elif isinstance(question, ChoiceQuestion):
        answer.choice = row.get("choice") if isinstance(row.get("choice"), str) else None
        answer.probabilities = {
            str(item["value"]): float(item["probability"])
            for item in probabilities
            if isinstance(item, dict) and "value" in item and _float(item.get("probability")) is not None
        }
    else:
        answer.score = _float(row.get("score"))
        labels = [level.label for level in question.levels]
        answer.probabilities = {
            str(item.get("label") or _level_label(labels, item.get("value"))): float(item["probability"])
            for item in probabilities
            if isinstance(item, dict) and _float(item.get("probability")) is not None
        }
    return answer


def _typesafe_question(question: DecisionQuestion) -> dict[str, Any]:
    if isinstance(question, PredicateQuestion):
        return {"type": "noul", "instructions": question.instructions}
    if isinstance(question, ChoiceQuestion):
        return {
            "type": "choice",
            "instructions": question.instructions,
            "criteria": {option.value: option.description for option in question.options},
        }
    return {
        "type": "score",
        "instructions": question.instructions,
        "criteria": [
            f"{level.label}: {level.description}" if level.description else level.label
            for level in question.levels
        ],
    }


def _typesafe_answer(row: dict[str, Any], question: DecisionQuestion) -> DecisionAnswer:
    if row.get("type") == "refusal":
        return DecisionAnswer(name=question.name, type=question.type, refused=True)
    answer = DecisionAnswer(name=question.name, type=question.type, confidence=_float(row.get("confidence")))
    probabilities = _probability_map(row.get("probabilities"))
    if isinstance(question, PredicateQuestion):
        # TypeSafe documents the yes/no value as "noul"; Microsoft's Foundry docs don't name it.
        answer.probability = _probability(row.get("noul", row.get("probability")))
        answer.missing = answer.probability is None
        if answer.probability is not None and answer.confidence is None:
            # Noul has no confidence of its own; TypeSafe's docs suggest |2p - 1|.
            answer.confidence = abs(2 * answer.probability - 1)
    elif isinstance(question, ChoiceQuestion):
        answer.choice = row.get("choice") if isinstance(row.get("choice"), str) else None
        answer.probabilities = {
            str(key): float(value) for key, value in probabilities.items() if _float(value) is not None
        }
    else:
        answer.score = _float(row.get("score"))
        labels = [level.label for level in question.levels]
        answer.probabilities = {
            _level_label(labels, key): float(value)
            for key, value in probabilities.items()
            if _float(value) is not None
        }
    return answer


def _probability_map(raw: object) -> dict[str, object]:
    """``{option: p}``, also from the ``[{"value"|"label": …, "probability": p}]`` list shape."""
    if isinstance(raw, dict):
        return {str(key): value for key, value in raw.items()}
    if isinstance(raw, list):
        return {
            str(item.get("value", item.get("label"))): item.get("probability")
            for item in raw
            if isinstance(item, dict) and ("value" in item or "label" in item)
        }
    return {}


def _level_label(labels: list[str], index: object) -> str:
    try:
        return labels[int(index)]  # type: ignore[call-overload]
    except (TypeError, ValueError, IndexError):
        return str(index)


def _fill_missing(
    answers: dict[str, DecisionAnswer], questions: Sequence[DecisionQuestion]
) -> dict[str, DecisionAnswer]:
    for question in questions:
        answers.setdefault(question.name, DecisionAnswer(name=question.name, type=question.type, missing=True))
    return answers


def _usage(raw: object) -> DecisionUsage:
    if not isinstance(raw, dict):
        return DecisionUsage()
    return DecisionUsage(
        input_tokens=raw.get("input_tokens") if isinstance(raw.get("input_tokens"), int) else None,
        output_tokens=raw.get("output_tokens") if isinstance(raw.get("output_tokens"), int) else None,
    )
