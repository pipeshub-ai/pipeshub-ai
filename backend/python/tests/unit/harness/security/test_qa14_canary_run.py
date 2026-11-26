"""QA14-2, QA14-3 (TSC-22): one durable run touching every tool source, scanned on every surface it writes to.

The real assembler, ``RunDriver`` and kernel run a connector read, a connector write that fails with its token in
the error text (as an HTTP client's URL or header does), an MCP tool, the knowledge base and ``run_code`` in the
local sandbox. The toolset and the MCP instance hold credentials the effect boundary must receive and nothing
else may: the journal (events, sealed content, checkpoints), the chat's live frames, the activity frames and the
stored command log, every log record at DEBUG, the model's prompts and the run context. A person's private text
(in a document the knowledge base returns, and in the program's output) belongs on their own surfaces only.
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
from app.harness.assembly.followups import parse_followups
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
    private_canary,
    secret_canary,
)
from harness_core.testing.durable import ScriptedModelPort
from harness_core.testing.ids import SeededIds
from tests.harness_replay.artifacts import ParityArtifactRegistry
from tests.support.sandbox_env import sandbox_backend, sandbox_env
from tests.support.token_vector import EXAMPLES
from tests.unit.harness.assembly import world as w
from tests.unit.harness.credentials.test_leak import (
    JIRA_TOKEN,
    MCP_TOKEN,
    _Services,  # pyright: ignore[reportPrivateUsage]
    seen,  # noqa: F401  # pyright: ignore[reportUnusedImport] - the fixture
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.unit.harness.credentials.test_leak import (
        _Seen,  # pyright: ignore[reportPrivateUsage]
    )

pytestmark = pytest.mark.usefixtures("flow_guard_off")

IDS = SeededIds(1414)
CANARY = secret_canary(IDS.uuid4)
PRIVATE_DOC = private_canary(IDS.uuid4)
PRIVATE_OUTPUT = private_canary(IDS.uuid4)
# Vendor shapes a program prints and a command line carries: the sandbox is the person's, its output is shown.
PRINTED = [example for _, example, entry in EXAMPLES if entry.forbidden]
CANARIES = Canaries(secrets=(JIRA_TOKEN, MCP_TOKEN, CANARY, *PRINTED), private=(PRIVATE_DOC, PRIVATE_OUTPUT))
# The program prints tokens it read (an env dump, a config file): encoded here, so only its output carries them.
CODE = "\n".join(
    [
        "import base64",
        f"print({PRIVATE_OUTPUT!r})",
        *(f"print('key: ' + base64.b64decode({base64.b64encode(e.encode()).decode()!r}).decode())" for e in PRINTED),
        "",
    ]
)


class _CanaryServices(_Services):
    """The toolset's stored credentials carry a canary beside its token, as an OAuth client secret would."""

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
def every_source(seen: _Seen, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Seen]:  # noqa: F811
    async def failing_create(self: w.FakeJira, title: str) -> str:
        token = self.client.token  # type: ignore[attr-defined]
        raise RuntimeError(f"401 Unauthorized for https://jira.example.test/rest/api/2/issue?access_token={token}")

    async def private_search(self: w.FakeKnowledgeGraph, query: str) -> str:
        return f'{{"hits": ["{query}: {PRIVATE_DOC}"]}}'

    w.replace_tool(monkeypatch, w.FakeJira, "create_issue", failing_create)
    w.replace_tool(monkeypatch, w.FakeKnowledgeGraph, "search", private_search)
    with ExitStack() as stack:
        stack.enter_context(
            patch("app.services.artifact_registry.ArtifactRegistryService", new=ParityArtifactRegistry(w.ORG, "ses-leak"))
        )
        stack.enter_context(patch.dict(os.environ, {**sandbox_env("host"), "SANDBOX_ALLOW_NETWORK": "false"}))
        stack.enter_context(sandbox_backend("host"))
        yield seen


def _log_text(record: logging.LogRecord) -> str:
    return f"{record.getMessage()} {getattr(record, 'harness', '')} {record.exc_text or ''}"


async def test_qa14_2_a_run_through_every_tool_source_leaks_nothing(
    every_source: _Seen, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    clock = ManualClock()
    journal = w.pipeshub_journal(clock=clock, lease_ms=30_000)
    tools = [f"jira@{w.JIRA}/search_issues", f"jira@{w.JIRA}/create_issue", f"mcp@{w.MCP}/list_prs"]
    spec = w.pinned_spec(tool_ids=tools, mcp=[w.MCP], sandbox="agent-code")
    journal.admit("run-leak", org_id=w.ORG, session_id="ses-leak", spec=spec, input_text="triage the board")
    port = ScriptedModelPort(
        ScriptedModel(
            ModelScript(
                id="qa14",
                turns=[
                    Turn(reply=Reply(tool_calls=[ToolCallSpec(name="jira__search_issues", args={"jql": "open"})])),
                    Turn(reply=Reply(tool_calls=[ToolCallSpec(name="jira__create_issue", args={"title": "t"})])),
                    Turn(reply=Reply(tool_calls=[ToolCallSpec(name="mcp_github_list_prs", args={"repo": "a"})])),
                    Turn(reply=Reply(tool_calls=[ToolCallSpec(name="knowledgegraph__search", args={"query": "q"})])),
                    Turn(reply=Reply(tool_calls=[ToolCallSpec(name="run_code", args={"code": CODE, "language": "python"})])),
                    Turn(reply=Reply(text="done")),
                ],
            )
        )
    )
    live, activity = InMemoryLiveBus(), InMemoryLiveBus()
    assembler = PipesHubRuntimeAssembler(_CanaryServices(), model_factory=w.model_factory(port), activity=activity)
    driver = RunDriver(journal=journal, assembler=w.ClaimAssembler(assembler), clock=clock, live=live)
    done = await driver.run_claimed(await w.claim_next(journal, "run-leak"))
    events = await journal.read("ses-leak")

    assert done.status == "completed", done
    outcomes = [e for e in events if e.type == "tool.outcome"]
    assert len(outcomes) == 5, [e.data for e in outcomes]
    assert every_source.factory.tokens == [JIRA_TOKEN]  # type: ignore[attr-defined]
    frames = await activity.read("run-leak")
    shown = "".join(str(f.data.get("text", "")) for f in frames if f.kind == "command_output")
    assert PRIVATE_OUTPUT in shown and SECRET_MASK in shown, "the program ran and its output was shown, masked"

    assert_no_canary(
        [
            Surface("journal (events, content, checkpoints)", journal, owner_view=True),
            Surface("live frames", await live.read("run-leak"), owner_view=True),
            Surface("activity frames", frames, owner_view=True),
            Surface("model prompts", port.calls, owner_view=True),
            Surface("logs", [_log_text(r) for r in caplog.records]),
        ],
        CANARIES,
    )


@pytest.mark.parametrize(("name", "example"), [(n, e) for n, e, _ in EXAMPLES], ids=[n for n, _, _ in EXAMPLES])
def test_qa14_2_follow_up_suggestions_never_carry_a_token(name: str, example: str) -> None:
    del name
    reply = f'["Rotate {example} today", "Check who used {example}", "Summarise the incident"]'
    items = parse_followups(reply, "what happened?")
    assert len(items) >= 2
    assert_no_canary([Surface("follow-up chips", items)], Canaries(secrets=(example,)))

