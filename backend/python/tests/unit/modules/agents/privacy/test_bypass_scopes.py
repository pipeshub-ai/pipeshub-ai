"""The model calls that do not go through a request's transports run inside
their own scope, so the request's ledger knows about each one."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import patch

from langchain_core.messages import AIMessage

from app.modules.agents.privacy.ledger import CallKind, current_model_call


class _RecordingModel:
    """Answers like a chat model and notes the scope each call ran in."""

    def __init__(self, reply: Any) -> None:  # noqa: ANN401
        self.reply = reply
        self.scopes: list[Any] = []

    async def ainvoke(self, *_: object, **__: object) -> Any:  # noqa: ANN401
        self.scopes.append(current_model_call())
        return self.reply


async def test_the_entity_verifier_runs_in_its_scope() -> None:
    from app.modules.named_entities.linking.verifier import (
        LlmMentionVerifier,
        OfferedCandidate,
        VerifyItem,
    )

    model = _RecordingModel(AIMessage(content="[]"))

    async def llm() -> tuple[Any, str]:
        return model, "m"

    await LlmMentionVerifier(llm).verify(
        "who is Ana?", [VerifyItem("Ana", None, [OfferedCandidate("Ana Ruiz", "person", "1 record")])],
    )

    assert [(s.site, s.kind) for s in model.scopes] == [("entity_verifier", CallKind.INSIDE_TOOL)]


async def test_grep_generation_runs_in_its_scope() -> None:
    from app.utils import pattern_match

    model = _RecordingModel({"parsed": None, "raw": AIMessage(content="")})
    with patch("app.utils.streaming._apply_structured_output", return_value=model):
        await pattern_match.generate_grep_command_via_llm("rate limits", object(), logging.getLogger("t"))

    assert model.scopes and all((s.site, s.kind) == ("grep_generation", CallKind.INSIDE_TOOL) for s in model.scopes)


