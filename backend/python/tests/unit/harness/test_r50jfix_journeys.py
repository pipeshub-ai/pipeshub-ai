"""R50-JFIX's runner faults, held to what the product promises (deployment/e2e/journeys.py).

R50JFIX-5: the assistant's search modes are knowledge-only or web-only (run-spec.ts ``assistantGrant``), so the runner
drives no MCP, toolset, pinned-skill or approval probe there. R50JFIX-6: a second /mcp call that joins a running task
answers at once that it was added (chat-turns.ts ``QUEUED_REPLY``). R50JFIX-8: a hold is released only when one is held,
or the scripted model's next hold passes straight through. R50JFIX-11: the default assistant's Slack bot answers a
lookup on its loop, which runs no grounding check.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

E2E = Path(__file__).resolve().parents[5] / "deployment" / "e2e"
sys.path.insert(0, str(E2E))
import journeys  # noqa: E402

NODE_CHAT_TURNS = (
    Path(__file__).resolve().parents[4] / "nodejs/apps/src/modules/agent-harness/application/chat-turns.ts"
)
MODE_TOOLS = ("mcp.first_use", "mcp.find_read", "mcp.hub", "mcp.oauth", "skills.preload", "toolset", "approval.ask",
              "approval.allow", "approval.block", "approval.org_rule", "approval.flow_guard")


def test_r50jfix5_the_search_modes_run_no_mcp_toolset_pinned_skill_or_approval_probe() -> None:
    assert journeys.consistent() == []
    for feature in MODE_TOOLS:
        assert journeys.CHAT_MODE_PATH not in journeys.SUPPORT[feature], feature
    for feature in ("knowledge", "web", "skills.load", "memory", "budget_partial", "steering"):
        assert journeys.CHAT_MODE_PATH in journeys.SUPPORT[feature], feature


def test_r50jfix6_the_queued_reply_is_nodes_own_words() -> None:
    assert f"export const QUEUED_REPLY = '{journeys.QUEUED_REPLY}';" in NODE_CHAT_TURNS.read_text()


def test_r50jfix6_what_pipeshub_chat_answered_is_read_from_its_text_content() -> None:
    reply = {"conversationId": "c1", "status": "Inprogress", "answer": journeys.QUEUED_REPLY}
    answered = {"result": {"content": [{"type": "text", "text": json.dumps(reply)}]}}
    assert journeys.mcp_result(answered) == reply
    assert journeys.mcp_result({"result": {"content": [{"type": "text", "text": "Too many agent runs"}]}}) == {
        "answer": "Too many agent runs"}
    assert journeys.mcp_result(None) == {}


class _Model:
    def __init__(self, held: bool) -> None:
        self.held = held
        self.posts: list[str] = []

    def ok(self, method: str, path: str) -> Any:  # noqa: ANN401
        assert (method, path) == ("GET", "/__control/holding")
        return {"holding": self.held}

    def request(self, method: str, path: str, body: Any) -> None:  # noqa: ANN401
        self.posts.append(path)


def test_r50jfix8_a_hold_is_released_only_when_one_is_held() -> None:
    for held in (True, False):
        model = _Model(held)
        ctx = SimpleNamespace(stack=SimpleNamespace(model=model))
        assert journeys.release_held(ctx) is held  # type: ignore[arg-type]
        assert model.posts == (["/__control/release"] if held else [])


def test_r50jfix11_the_slack_bots_answers_are_not_held_to_the_grounding_check() -> None:
    assert "surface.slack" not in journeys.SUPPORT["answer_contract"]
