"""SEC4-7: a resolved secret with no vendor shape and no secret-named key around it, masked on every surface.

The toolset's stored record holds a ``pipeshub-canary-<uuid>`` client secret. The vector cannot see it: it has no
vendor shape, and where a tool puts it (a result, an error message, a program's output) no key names it. The effect
boundary resolved it, so the real assembler, ``RunDriver`` and kernel mask it exactly: the journal (outcomes,
presented results, the command record), live frames, activity frames, the model's prompts and logs.
"""

from __future__ import annotations

import base64
import logging
import os
from contextlib import ExitStack
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from harness_core.driver import RunDriver
from harness_core.journal.memory import InMemoryLiveBus
from harness_core.security.token_patterns import SECRET_MASK
from harness_core.testing import (
    ManualClock,
    ModelScript,
    Reply,
    ScriptedModel,
    ToolCallSpec,
    Turn,
)
from harness_core.testing.canary import (
    Canaries,
    Surface,
    assert_no_canary,
    secret_canary,
    strings_in,
)
from harness_core.testing.durable import ScriptedModelPort
from harness_core.testing.ids import SeededIds
from tests.harness_replay.artifacts import ParityArtifactRegistry
from tests.support.sandbox_env import sandbox_backend, sandbox_env
from tests.unit.harness.assembly import world as w
from tests.unit.harness.credentials.test_leak import (
    JIRA_TOKEN,
    _Services,  # pyright: ignore[reportPrivateUsage]
    seen,  # noqa: F401  # pyright: ignore[reportUnusedImport] - the fixture
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.unit.harness.credentials.test_leak import (
        _Seen,  # pyright: ignore[reportPrivateUsage]
    )

pytestmark = pytest.mark.usefixtures("flow_guard_off")

IDS = SeededIds(4077)
CANARY = secret_canary(IDS.uuid4)
# The program prints the value it read from somewhere the model never saw: only its output carries it.
CODE = f"import base64\nprint('cfg: ' + base64.b64decode({base64.b64encode(CANARY.encode()).decode()!r}).decode())\n"


class _ClientSecretServices(_Services):
    def __init__(self) -> None:
        super().__init__()
        self.config.answer(
            "/services/toolsets/",
            lambda _path: {
                "isAuthenticated": True,
                "credentials": {"access_token": JIRA_TOKEN, "client_secret": CANARY},
                "oauthConfigId": "oc-1",
            },
        )


@pytest.fixture
def echoing(seen: _Seen, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Seen]:  # noqa: F811
    async def echoed_search(self: w.FakeJira, jql: str) -> str:
        return f'{{"issues": [], "debug": "client {CANARY} queried {jql}"}}'

    async def failing_create(self: w.FakeJira, title: str) -> str:
        raise RuntimeError(f"invalid_client: {CANARY} was rejected")

    w.replace_tool(monkeypatch, w.FakeJira, "search_issues", echoed_search)
    w.replace_tool(monkeypatch, w.FakeJira, "create_issue", failing_create)
    with ExitStack() as stack:
        stack.enter_context(
            patch("app.services.artifact_registry.ArtifactRegistryService", new=ParityArtifactRegistry(w.ORG, "ses-sec4"))
        )
        stack.enter_context(patch.dict(os.environ, {**sandbox_env("host"), "SANDBOX_ALLOW_NETWORK": "false"}))
        stack.enter_context(sandbox_backend("host"))
        yield seen


async def test_sec4_7_an_unshaped_resolved_secret_is_masked_on_every_surface(
    echoing: _Seen, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    clock = ManualClock()
    journal = w.pipeshub_journal(clock=clock, lease_ms=30_000)
    spec = w.pinned_spec(tool_ids=[f"jira@{w.JIRA}/search_issues", f"jira@{w.JIRA}/create_issue"], sandbox="agent-code")
    journal.admit("run-sec4", org_id=w.ORG, session_id="ses-sec4", spec=spec, input_text="triage the board")
    port = ScriptedModelPort(
        ScriptedModel(
            ModelScript(
                id="sec4",
                turns=[
                    Turn(reply=Reply(tool_calls=[ToolCallSpec(name="jira__search_issues", args={"jql": "open"})])),
                    Turn(reply=Reply(tool_calls=[ToolCallSpec(name="jira__create_issue", args={"title": "t"})])),
                    Turn(reply=Reply(tool_calls=[ToolCallSpec(name="run_code", args={"code": CODE, "language": "python"})])),
                    Turn(reply=Reply(text="done")),
                ],
            )
        )
    )
    live, activity = InMemoryLiveBus(), InMemoryLiveBus()
    assembler = PipesHubRuntimeAssembler(_ClientSecretServices(), model_factory=w.model_factory(port), activity=activity)
    driver = RunDriver(journal=journal, assembler=w.ClaimAssembler(assembler), clock=clock, live=live)
    done = await driver.run_claimed(await w.claim_next(journal, "run-sec4"))
    events = await journal.read("ses-sec4")

    assert done.status == "completed", done
    outcomes = [e for e in events if e.type == "tool.outcome"]
    assert len(outcomes) == 3, [e.data for e in outcomes]
    frames = await activity.read("run-sec4")
    shown = "".join(str(f.data.get("text", "")) for f in frames if f.kind == "command_output")
    assert f"cfg: {SECRET_MASK}" in shown, "the program ran and its output was shown, masked"
    prompts = "\n".join(strings_in(port.calls))
    assert f"client {SECRET_MASK} queried open" in prompts, "a successful result reaches the model, masked"
    assert f"invalid_client: {SECRET_MASK} was rejected" in prompts, "the failure reaches the model, masked"

    assert_no_canary(
        [
            Surface("journal (events, content, checkpoints)", journal, owner_view=True),
            Surface("live frames", await live.read("run-sec4"), owner_view=True),
            Surface("activity frames", frames, owner_view=True),
            Surface("model prompts", port.calls, owner_view=True),
            Surface("logs", [f"{r.getMessage()} {getattr(r, 'harness', '')} {r.exc_text or ''}" for r in caplog.records]),
        ],
        Canaries(secrets=(CANARY, JIRA_TOKEN)),
    )
