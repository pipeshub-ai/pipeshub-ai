"""WIP-1..WIP-6 (OWD-18): who a run's external writes are from is the ``WriteIdentityPort`` extension point.

- WIP-1: this build's port is ``UserWriteIdentity``: every write as the user, with the fixed AI disclosure naming the
  run's agent (the assistant's name when it has none) and linking its session, whatever the pin says per action type.
- WIP-2: its ``prepare`` changes nothing and its ``tools`` hands every instance's tools back as the loader built them.
- WIP-3: each action type has its own sentence; a disclosure target of every action reads its own wording.
- WIP-4: one of several instances of a type is disclosed by the path its tool declares.
- WIP-5: the settings an edition's port answers decide: a refusal denies the call before it reaches the connector, and
  an overridden sentence and trailer are what the write carries.
- WIP-6: the port is reached where the loop and the engines load their tools: ``prepare`` before ``load_run_tools``,
  ``settings`` after, and the loader asks ``tools`` for each bound instance.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, override

import pytest

from app import edition_services
from app.harness.write_identity.disclosure import (
    ASSISTANT,
    DISCLOSURES,
    WriteIdentitySettings,
    disclosed,
    render,
    write_identity_middleware,
)
from app.harness.write_identity.ports import (
    USER_WRITE_IDENTITY,
    UserWriteIdentity,
    WriteIdentityPort,
    write_identity_port,
)
from app.harness.write_identity.settings import use_write_identity
from app.harness.write_identity.targets import DisclosureTarget
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.hooks.middleware.context import ToolCallContext
from harness_core.kernel.hooks.middleware.decisions import PreDecision
from harness_core.kernel.hooks.registry import HookRegistry
from harness_core.logs import HarnessLog
from harness_core.tools.base import ParameterType, ToolOutput, ToolParameter
from harness_core.tools.decorators import tool
from harness_core.tools.executor import ToolExecutor
from harness_core.tools.registry import ToolRegistry
from harness_core.tools.toolset import ToolsetBuilder
from tests.support.harness_edition import default_harness_edition  # noqa: F401 - the fixture, used by name

pytestmark = pytest.mark.usefixtures("default_harness_edition")

if TYPE_CHECKING:
    from harness_core.contracts.generated.enums import WriteAction

APP = "https://pipeshub.example.com"
ACTIONS: tuple[WriteAction, ...] = ("email", "message", "ticket", "document", "event", "commit")


class _Config:
    async def get_config(self, key: str, default: object = None, raise_on_error: bool = True) -> object:
        return {"frontend": {"publicEndpoint": APP}}


def _env(agent: object, config: object | None = None) -> Any:  # noqa: ANN401 - a ToolEnvironment stand-in
    return SimpleNamespace(agent=agent, context=SimpleNamespace(config_service=config))


def _run(agent: object = None) -> Any:  # noqa: ANN401 - a RunContext stand-in
    return SimpleNamespace(agent=agent, session_id="ses-1")


def settings() -> WriteIdentitySettings:
    return WriteIdentitySettings(session_id="ses-1", agent_name="Triage", app_url=APP)


class _Policy:
    def dimension(self, path: str) -> object:
        return {"inst-slack": "ops-bot"} if path.endswith(".bots") else "bot"


def test_wip1_this_build_reads_the_user_default() -> None:
    assert write_identity_port() is USER_WRITE_IDENTITY
    assert edition_services.harness_edition().supplied(WriteIdentityPort) is None  # type: ignore[type-abstract]
    assert isinstance(USER_WRITE_IDENTITY, UserWriteIdentity)


async def test_wip1_every_write_is_the_users_with_the_disclosure_whatever_the_pin_says() -> None:
    log = HarnessLog.named("t")
    port = write_identity_port()
    made = await port.settings(_run({"_key": "a1"}), _env({"name": " Triage "}, _Config()), _Policy(), log)  # type: ignore[arg-type]
    assert (made.session_id, made.agent_name, made.app_url) == ("ses-1", "Triage", APP)
    assert all(made.identity(action) == "user" for action in ACTIONS)
    assert all(made.co_author(action) is None and made.refusal(action, "/tools/slack/send_message") is None for action in ACTIONS)
    assert made.session_link == f"{APP}/chat?conversationId=ses-1"
    bare = await port.settings(_run(), _env({"name": "ignored"}), _Policy(), log)  # type: ignore[arg-type]
    assert (bare.agent_name, bare.app_url) == (ASSISTANT, None)


async def test_wip2_prepare_changes_nothing_and_tools_come_back_as_built() -> None:
    port = write_identity_port()
    context = SimpleNamespace(extension_state={})
    env = SimpleNamespace(context=context, agent=None)
    assert await port.prepare(_run(), env, _Policy(), HarnessLog.named("t")) is None  # type: ignore[arg-type]
    assert context.extension_state == {}
    built: list[object] = ["send", "read"]
    out = await port.tools(context, object(), object, "slack", "inst-slack", built, lambda *_: ["other"])  # type: ignore[arg-type]
    assert out == built and out is not built


@pytest.mark.parametrize("action", ACTIONS)
def test_wip3_each_action_type_has_its_own_sentence(action: WriteAction) -> None:
    assert render(settings(), "text", action).startswith(DISCLOSURES[action].format(agent="Triage"))
    assert DISCLOSURES["ticket"].startswith("Written via")
    assert DISCLOSURES["event"].startswith("Created via")
    assert DISCLOSURES["commit"].startswith("Committed via")


def test_wip3_a_disclosure_target_of_every_action_reads_its_own_wording() -> None:
    target = DisclosureTarget("event", "description", "html")
    arguments: dict[str, object] = {"description": "<p>Sync</p>"}
    assert disclosed(settings(), target, arguments)
    assert "Created via PipesHub agent Triage" in str(arguments["description"])


def _second_slack(sent: list[str]) -> tuple[ToolRegistry, str]:
    class Slack:
        @tool(
            path="/tools/slack/send_message",
            short_description="send",
            description="Send a Slack message.",
            parameters=[
                ToolParameter(name="channel", type=ParameterType.STRING, description="channel", required=True),
                ToolParameter(name="message", type=ParameterType.STRING, description="message", required=True),
            ],
        )
        async def send_message(self, channel: str, message: str) -> ToolOutput:
            sent.append(message)
            return ToolOutput(success=True, data={"ok": True})

    registry = ToolRegistry()
    toolset = ToolsetBuilder(
        Slack(),
        name="slack_9f3c",
        description="Slack (second)",
        path_prefix="/tools/slack_9f3c",
        rebase=lambda declared: declared.replace("/tools/slack/", "/tools/slack_9f3c/"),
    )
    for t in toolset.tools:
        registry.register_tool(t)
    [registered] = toolset.tools
    assert registered.path == "/tools/slack_9f3c/send_message"
    return registry, registered.name


@dataclass(frozen=True, slots=True)
class _Chosen(WriteIdentitySettings):
    """Settings an edition's port could answer: its own sentence and trailer, and a refusal for one path."""

    refused_path: str = ""

    @override
    def sentence_for(self, action: WriteAction) -> str:
        return f"Posted for the team by {self.agent_name}"

    @override
    def co_author(self, action: WriteAction) -> str | None:
        return "Reviewed-by: Alex" if action == "message" else None

    @override
    def refusal(self, action: WriteAction, tool_path: str, what: str | None = None) -> str | None:
        return "not sent this way" if tool_path == self.refused_path else None


async def test_wip4_one_of_several_instances_is_disclosed_by_the_path_its_tool_declares() -> None:
    sent: list[str] = []
    registry, name = _second_slack(sent)
    executor = ToolExecutor(registry, use_write_identity(HookRegistry(), settings(), registry))
    result = await executor.call_tool(ToolCall(id="c1", name=name, arguments={"channel": "C1", "message": "Shipped"}))
    assert not result.is_error
    assert sent == [f"Shipped\n\n{render(settings(), 'slack', 'message')}"]


async def test_wip5_a_refusal_never_reaches_the_connector() -> None:
    sent: list[str] = []
    registry, name = _second_slack(sent)
    refusing = _Chosen(session_id="ses-1", agent_name="Triage", app_url=APP, refused_path="/tools/slack_9f3c/send_message")
    executor = ToolExecutor(registry, use_write_identity(HookRegistry(), refusing, registry))
    result = await executor.call_tool(ToolCall(id="c2", name=name, arguments={"channel": "C1", "message": "Again"}))
    assert result.is_error and sent == []
    ctx = ToolCallContext(tool_path="/tools/slack_9f3c/send_message", tool_input={"channel": "C1", "message": "Hi"})

    async def next_fn() -> None:
        return None

    await write_identity_middleware(refusing, None, lambda _path: "/tools/slack/send_message")(ctx, next_fn)
    assert ctx.decision == PreDecision.DENY and ctx.decision_reason == "not sent this way"
    assert ctx.tool_input == {"channel": "C1", "message": "Hi"}


def test_wip5_the_chosen_sentence_and_trailer_are_what_the_write_carries() -> None:
    chosen = _Chosen(session_id="ses-1", agent_name="Triage", app_url=APP)
    arguments: dict[str, object] = {"text": "Shipped"}
    assert disclosed(chosen, DisclosureTarget("message", "text", "text"), arguments)
    text = str(arguments["text"])
    assert "Posted for the team by Triage" in text and text.endswith("Reviewed-by: Alex")
    assert "Sent via" not in text


def test_wip6_the_port_is_reached_where_the_loop_and_the_engines_load_their_tools() -> None:
    from app.agents.agent_loop import tool_loader
    from app.harness.assembly import assembler
    from app.harness.engines import durable

    for module in (assembler, durable):
        source = inspect.getsource(module)
        prepared = source.index("await write_identity_port().prepare(run, env, policy, log)")
        assert prepared < source.index("await load_run_tools(") < source.index("await write_identity_port().settings(")
    assert "write_identity_port().tools(" in inspect.getsource(tool_loader)
