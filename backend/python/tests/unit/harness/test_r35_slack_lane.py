"""R35-SLACK: the e2e lanes' scripted Slack workspace, the scripted model's Slack line breaks, and the journeys' judge
of an engine's lost output (R34FIX-FU-3).

* SL-1: the workspace answers the bot's Web API as Slack does: who a user is, a message posted, a streamed answer
  appended and stopped, a thread read back as a person sees it.
* SL-2: a delivered message reaches the bot signed with its signing secret, as the bot's receiver verifies it.
* SL-3: the scripted model reads a script the bot sent as one line.
* LO-1: a journey accepts an unread output only where the engine is known to lose output, the command exited 0 with
  nothing on either stream and the step is marked so (the person reads "No output was captured."); any output, an
  unmarked step or another engine is not accepted.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import runpy
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from starlette.testclient import TestClient

E2E = Path(__file__).resolve().parents[5] / "deployment" / "e2e"
sys.path.insert(0, str(E2E))
import journeys  # noqa: E402

if TYPE_CHECKING:
    import urllib.request

CONTROL = "slack-lane-control"
BOT = {"Authorization": "Bearer xoxb-e2e-abc123"}


@pytest.fixture
def slack(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("FAKE_SAAS_CONTROL_TOKEN", CONTROL)
    module = runpy.run_path(str(E2E / "fake_saas" / "server.py"), run_name="fake_saas_under_test")
    fake_slack = sys.modules["fake_slack"]
    fake_slack.USERS.clear()
    fake_slack.MESSAGES.clear()
    fake_slack.CALLS.clear()
    return TestClient(module["app"]())


def test_sl1_the_workspace_answers_the_web_api_as_slack(slack: TestClient) -> None:
    control = {"X-E2E-Control": CONTROL}
    slack.put("/__control/slack/users", json={"U0ANA": {"email": "ana@acme.test", "name": "Ana"}}, headers=control)
    me = slack.post("/slack/api/users.info", data={"user": "U0ANA"}, headers=BOT).json()
    assert me["user"]["profile"]["email"] == "ana@acme.test"
    assert slack.post("/slack/api/auth.test", headers=BOT).json()["user_id"] == "UABC123"
    assert slack.post("/slack/api/chat.postMessage", data={"channel": "C1"}).json() == {"ok": False, "error": "not_authed"}
    started = slack.post("/slack/api/chat.startStream", headers=BOT,
                         data={"channel": "C1", "thread_ts": "1.0", "markdown_text": "Hel"}).json()
    slack.post("/slack/api/chat.appendStream", headers=BOT, data={"channel": "C1", "ts": started["ts"], "markdown_text": "lo"})
    slack.post("/slack/api/chat.stopStream", headers=BOT, data={"channel": "C1", "ts": started["ts"]})
    slack.post("/slack/api/chat.postMessage", headers=BOT,
               data={"channel": "C1", "thread_ts": "1.0", "text": "", "blocks": json.dumps([{"type": "section"}])})
    thread = slack.get("/__control/slack/thread?channel=C1&ts=1.0", headers=control).json()["messages"]
    assert [(m["markdown"], m["blocks"], m["streaming"]) for m in thread] == [
        ("Hello", [], False), ("", [{"type": "section"}], False)]
    assert slack.get("/__control/slack/thread?channel=C1&ts=1.0").status_code == 401


def test_sl2_a_message_reaches_the_bot_signed_with_its_secret(slack: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_slack = sys.modules["fake_slack"]
    sent: dict[str, Any] = {}

    class _Answer:
        status = 200

        def __enter__(self) -> _Answer:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    def urlopen(request: urllib.request.Request, timeout: float) -> _Answer:
        sent.update(body=request.data, headers=dict(request.header_items()))
        return _Answer()

    monkeypatch.setattr(fake_slack.urllib.request, "urlopen", urlopen)
    answer = slack.post("/__control/slack/deliver", headers={"X-E2E-Control": CONTROL}, json={
        "signingSecret": "s3cret", "botToken": "xoxb-e2e-abc123", "user": "U0ANA", "text": "hello"}).json()
    assert answer["status"] == 200
    stamp = sent["headers"]["X-slack-request-timestamp"]
    want = "v0=" + hmac.new(b"s3cret", f"v0:{stamp}:".encode() + sent["body"], hashlib.sha256).hexdigest()
    assert sent["headers"]["X-slack-signature"] == want
    event = json.loads(sent["body"])["event"]
    assert (event["type"], event["text"], event["ts"]) == ("app_mention", "<@UABC123> hello", answer["thread_ts"])


def test_sl3_the_scripted_model_reads_a_script_sent_as_one_line() -> None:
    os.environ.setdefault("FAKE_LLM_API_KEY", "k")
    os.environ.setdefault("FAKE_LLM_CONTROL_TOKEN", "c")
    import importlib.util

    spec = importlib.util.spec_from_file_location("r35_fake_llm_server", E2E / "fake_llm" / "server.py")
    assert spec is not None and spec.loader is not None
    fake_llm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fake_llm)
    message = fake_llm.ChatMessage(role="user", content="<@UBOT> ¶ tool lookup_note {} ¶ say done")
    (read,) = fake_llm._slack_lines([message])  # pyright: ignore[reportPrivateUsage]
    assert read.content.splitlines() == ["<@UBOT>", "tool lookup_note {}", "say done"]
    # R36-COVER: a reply in a thread carries the thread's earlier messages ahead of it; only the reply is this
    # turn's script, as a model answers the current message (the question asked again otherwise).
    for marker in ("Current slack message/query: ", "Current user's message: "):
        reply = fake_llm.ChatMessage(role="user", content=(
            "Slack thread context:\nMorgan: <@UBOT> ¶ tool ask_user_question {} ¶ say q\n\n"
            f"{marker}<@UBOT> Teal ¶ say answered"))
        (read,) = fake_llm._slack_lines([reply])  # pyright: ignore[reportPrivateUsage]
        assert read.content.splitlines() == ["<@UBOT> Teal", "say answered"]


class _Stack:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events

    def journal(self, _session: str) -> dict[str, Any]:
        return {"events": self.events}


def _finished(command: str, *, stdout: int = 0, stderr: int = 0, marked: bool = True, exit_code: int = 0) -> dict[str, Any]:
    data: dict[str, Any] = {"command": command, "status": "completed" if exit_code == 0 else "failed",
                            "exitCode": exit_code, "stdout": {"bytes": stdout}, "stderr": {"bytes": stderr}}
    if marked:
        data["outputMayBeLost"] = True
    return {"type": "x.pipeshub.command.finished", "data": data}


@pytest.mark.parametrize(("engine_path", "event", "accepted"), [
    ("job.acp-qwen", _finished("cat a.txt"), True),
    ("job.acp-qwen", _finished("cat a.txt", stdout=12), False),
    ("job.acp-qwen", _finished("cat a.txt", stderr=40), False),
    ("job.acp-qwen", _finished("cat a.txt", marked=False), False),
    ("job.acp-qwen", _finished("cat a.txt", exit_code=1), False),
    ("job.acp-qwen", _finished("cat b.txt"), False),
    ("job.opencode", _finished("cat a.txt"), False),
])
def test_lo1_an_unread_output_passes_only_as_the_engine_s_honest_loss(
    engine_path: str, event: dict[str, Any], *, accepted: bool,
) -> None:
    ctx = journeys.Ctx(_Stack([event]), {}, engine_path, "owner", "m", {})
    turn = journeys.Turn([], "s", "r", "", None)
    assert journeys.lost_output(ctx, turn, "cat a.txt") is accepted
