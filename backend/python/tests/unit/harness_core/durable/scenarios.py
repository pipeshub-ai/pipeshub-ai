"""Scripts shared by the in-process tests and the kill -9 child."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.testing import ModelScript, ScriptedModel
from harness_core.testing.durable import ScriptedModelPort

from .support import reply, scripted

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class Scenario:
    child: Callable[[], tuple[ScriptedModel, ScriptedModelPort]]
    resume: Callable[[], tuple[ScriptedModel, ScriptedModelPort]]
    failpoint: str


def _none() -> tuple[ScriptedModel, ScriptedModelPort]:
    model = ScriptedModel(ModelScript(id="empty", turns=[]))
    return model, ScriptedModelPort(model)


SCENARIOS: dict[str, Scenario] = {
    "k2": Scenario(
        child=lambda: scripted(reply("the answer", reasoning="private chain")),
        resume=_none,
        failpoint="worker.model.after_response_commit",
    ),
    "k4-idempotent": Scenario(
        child=lambda: scripted(reply("", ("create_issue", {"title": "bug"}))),
        resume=lambda: scripted(reply("filed")),
        failpoint="worker.tool.after_effect",
    ),
    "k4-non-idempotent": Scenario(
        child=lambda: scripted(reply("", ("post_message", {"text": "hi"}))),
        resume=lambda: scripted(reply("could not confirm")),
        failpoint="worker.tool.after_effect",
    ),
}
