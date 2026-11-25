"""``FakeEngine``: a scriptable in-memory ``EngineAdapter`` and ``EngineSession``.

It runs the shared agent loop "inside" a ``FakeSandboxHandle``: it reads the
launch it rendered, reaches the model and tool gateways through the sandbox's
egress with the token placeholder, and emits neutral ``EngineEvent``s directly.
It is the reference the conformance suite is proven on, and the engine a
controller test uses when it does not care which engine runs.
"""

from __future__ import annotations

import asyncio
import json
import posixpath
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Literal, cast, override

from harness_core.access.types import ENGINE_MODEL_ID, ModelApi
from harness_core.context.user_compact import CompactReport
from harness_core.engines.errors import (
    CompactFailed,
    EngineSessionClosed,
    EngineStateIncompatible,
    SteeringUnsupported,
)
from harness_core.engines.events import (
    CommandFinished,
    CommandOutput,
    CommandStarted,
    EngineError,
    EngineEvent,
    EngineState,
    FileChanged,
    MessageCompleted,
    SubAgentFinished,
    SubAgentStarted,
    TextDelta,
    ToolCallSeen,
    TurnCompleted,
    UsageReported,
)
from harness_core.engines.jsonview import as_int, as_list, as_object, as_str
from harness_core.engines.policy import NativeTool, expected_native_tools
from harness_core.engines.stream import EventBuffer, TurnSeq
from harness_core.engines.types import (
    EngineCapabilities,
    EngineKind,
    EngineLaunch,
    EngineProfile,
    EngineTask,
    EngineVersion,
    FollowUp,
    GatewayEndpoints,
    JsonValue,
    SessionHealth,
)
from harness_core.sandbox.tar import put_files
from harness_core.testing.engines.loop import (
    ModelEndpoint,
    ModelRefused,
    ToolOutcome,
    call_gateway_tool,
    call_model,
    list_gateway_tools,
    run_agent_loop,
)
from harness_core.testing.engines.script import (
    SUBAGENT_TOOLS,
    ChatMessage,
    ModelUsage,
    ToolCall,
)
from harness_core.testing.sandbox.fake import FakeSandboxHandle

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Mapping

    from harness_core.interactions.ports import InteractionDecision
    from harness_core.sandbox import SandboxHandle

    # Runs a shell command over the sandbox's files (``workdir`` is the engine's working folder); unset, the
    # shell is inert, as the conformance suite needs no program to run.
    ShellRunner = Callable[[FakeSandboxHandle, str, str], Awaitable[ToolOutcome]]

CONFIG_PATH = "/state/fake-engine/config.json"
TOKEN_ENV = "FAKE_ENGINE_TOKEN"
TOOLS_TOKEN_ENV = "FAKE_ENGINE_TOOLS_TOKEN"
STATE_SCHEMA = 1
BASE_PROMPT = "You are a coding engine working in the workspace folder."
NATIVE_NAMES: Mapping[NativeTool, str] = {
    NativeTool.READ: "read",
    NativeTool.SEARCH: "search",
    NativeTool.EDIT: "write",
    NativeTool.SHELL: "shell",
    NativeTool.SKILL: "skill",
    NativeTool.SUBAGENT: "subagent",
}
FAKE_VERSION = EngineVersion(kind="jcode", version="0.0.0-fake", binary_digest="sha256:" + "0" * 64)


@dataclass(frozen=True, slots=True)
class FakeEngineConfig:
    engine: EngineVersion
    native_tools: tuple[str, ...]
    model_base_url: str
    tools_url: str
    tool_timeout_s: int
    sub_agents_enabled: bool
    sub_agent_depth: int
    skills_dir: str
    max_steps: int

    def to_json(self) -> bytes:
        body: dict[str, JsonValue] = {
            "engine": {
                "kind": self.engine.kind,
                "version": self.engine.version,
                "digest": self.engine.binary_digest,
            },
            "nativeTools": list(self.native_tools),
            "modelBaseUrl": self.model_base_url,
            "toolsUrl": self.tools_url,
            "toolTimeoutS": self.tool_timeout_s,
            "subAgents": {"enabled": self.sub_agents_enabled, "maxDepth": self.sub_agent_depth},
            "skillsDir": self.skills_dir,
            "maxSteps": self.max_steps,
        }
        return json.dumps(body, sort_keys=True).encode()

    @staticmethod
    def from_json(data: bytes) -> FakeEngineConfig:
        obj = as_object(json.loads(data))
        engine = as_object(obj.get("engine"))
        sub = as_object(obj.get("subAgents"))
        return FakeEngineConfig(
            engine=EngineVersion(
                kind=cast("EngineKind", as_str(engine.get("kind"))),
                version=as_str(engine.get("version")),
                binary_digest=as_str(engine.get("digest")),
            ),
            native_tools=tuple(as_str(t) for t in as_list(obj.get("nativeTools"))),
            model_base_url=as_str(obj.get("modelBaseUrl")),
            tools_url=as_str(obj.get("toolsUrl")),
            tool_timeout_s=as_int(obj.get("toolTimeoutS")),
            sub_agents_enabled=sub.get("enabled") is True,
            sub_agent_depth=as_int(sub.get("maxDepth")),
            skills_dir=as_str(obj.get("skillsDir")),
            max_steps=as_int(obj.get("maxSteps")),
        )


class FakeEngineAdapter:
    """In-memory ``EngineAdapter``. The kind set is closed, so the fake answers to ``jcode``."""

    kind: ClassVar[EngineKind] = "jcode"

    def __init__(
        self,
        *,
        version: EngineVersion = FAKE_VERSION,
        can_steer: bool = True,
        can_compact: bool = True,
        max_tools: int | None = None,
        max_steps: int = 20,
        shell: ShellRunner | None = None,
    ) -> None:
        self.version = version
        self._can_steer = can_steer
        self._can_compact = can_compact
        self._max_tools = max_tools
        self._max_steps = max_steps
        self._shell = shell

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            can_steer=self._can_steer,
            native_skills=True,
            sub_agents=True,
            model_apis=frozenset({ModelApi.CHAT_COMPLETIONS}),
            max_tools=self._max_tools,
            drive="stdio",
            can_compact=self._can_compact,
        )

    def render_launch(
        self, profile: EngineProfile, endpoints: GatewayEndpoints, skills_dir: str
    ) -> EngineLaunch:
        native = sorted(NATIVE_NAMES[t] for t in expected_native_tools(profile, skills=True))
        config = FakeEngineConfig(
            engine=profile.engine,
            native_tools=tuple(native),
            model_base_url=endpoints.model_base_url,
            tools_url=endpoints.tools_mcp_url,
            tool_timeout_s=endpoints.tool_timeout_s,
            sub_agents_enabled=profile.sub_agents.enabled,
            sub_agent_depth=profile.sub_agents.max_depth,
            skills_dir=skills_dir,
            max_steps=min(self._max_steps, profile.limits.max_steps or self._max_steps),
        )
        return EngineLaunch(
            engine=profile.engine,
            argv=("fake-engine", "--config", CONFIG_PATH),
            env={TOKEN_ENV: endpoints.token_placeholder, TOOLS_TOKEN_ENV: endpoints.tools_placeholder},
            files={CONFIG_PATH: config.to_json()},
        )

    async def open(
        self,
        sandbox: SandboxHandle,
        launch: EngineLaunch,
        restore: EngineState | None,
        *,
        correlation: Mapping[str, str] | None = None,
    ) -> FakeEngineSession:
        config = FakeEngineConfig.from_json(launch.files[CONFIG_PATH])
        history: list[ChatMessage] = []
        if restore is not None:
            history = decode_state(restore, launch.engine)
        if not isinstance(sandbox, FakeSandboxHandle):
            raise TypeError("FakeEngine runs only in a FakeSandboxHandle")
        await put_files(sandbox, launch.files)
        headers = {"authorization": f"Bearer {launch.env[TOKEN_ENV]}"}
        tool_headers = {"authorization": f"Bearer {launch.env.get(TOOLS_TOKEN_ENV, launch.env[TOKEN_ENV])}"}
        gateway_tools = await list_gateway_tools(sandbox.egress, config.tools_url, tool_headers)
        has_skills = any(p.startswith(config.skills_dir.rstrip("/") + "/") for p in sandbox.files)
        native = [
            t for t in config.native_tools if t != NATIVE_NAMES[NativeTool.SKILL] or has_skills
        ]
        return FakeEngineSession(
            sandbox=sandbox,
            config=config,
            headers=headers,
            tool_headers=tool_headers,
            tools=(*native, *(name for name, _ in gateway_tools)),
            history=history,
            can_steer=self._can_steer,
            workdir=launch.workdir,
            shell=self._shell,
        )


COMPACT_PROMPT = "Summarise the conversation so far so that the work can continue from the summary alone."
SUMMARY_PREFIX = "Summary of the earlier conversation:\n"


def _history_tokens(history: list[ChatMessage]) -> int:
    return sum(len(m.content) for m in history) // 4


def decode_state(state: EngineState, expected: EngineVersion) -> list[ChatMessage]:
    if state.engine != expected:
        raise EngineStateIncompatible("state was saved by another engine version")
    if state.schema_version != STATE_SCHEMA:
        raise EngineStateIncompatible(f"unknown state schema {state.schema_version}")
    obj = as_object(json.loads(state.payload))
    return [ChatMessage.from_json(m) for m in as_list(obj.get("history"))]


class _Hooks:
    # The helper whose steps these are (EV-07); none: the session's own agent.
    _helper: str | None = None

    def __init__(self, session: FakeEngineSession, seq: TurnSeq, out: EventBuffer) -> None:
        self._session = session
        self._seq = seq
        self._out = out
        self._messages = 0

    async def step_started(self) -> None:
        return None

    async def text(self, text: str) -> None:
        self._messages += 1
        message_id = f"{self._seq.turn_id}:m{self._messages}"
        if text:
            await self._out.put(TextDelta(self._seq.next(), message_id, text))
        await self._out.put(MessageCompleted(self._seq.next(), message_id, text))

    async def tool_started(self, call: ToolCall) -> None:
        kind = self._session.tool_kind(call.name)
        helper = self._helper
        if kind == "shell":
            command = as_str(call.args().get("command"))
            await self._out.put(CommandStarted(self._seq.next(), call.id, command, sub_session_id=helper))
        elif kind == "subagent" and helper is None:
            asked = as_str(call.args().get("prompt"))
            await self._out.put(SubAgentStarted(self._seq.next(), self._sub_id(call), call.id, "helper", asked))
        elif kind == "gateway":
            await self._out.put(
                ToolCallSeen(self._seq.next(), call.id, call.name, "started", sub_session_id=helper, arguments=_asked(call))
            )

    async def tool_finished(self, call: ToolCall, outcome: ToolOutcome) -> None:
        kind = self._session.tool_kind(call.name)
        helper = self._helper
        status: Literal["completed", "failed"] = "failed" if outcome.is_error else "completed"
        if kind == "shell":
            await self._out.put(CommandOutput(self._seq.next(), call.id, outcome.content, sub_session_id=helper))
            await self._out.put(
                CommandFinished(
                    self._seq.next(), call.id, None if outcome.is_error else 0, status, sub_session_id=helper
                )
            )
        elif kind == "write" and not outcome.is_error:
            path = as_str(call.args().get("filePath"))
            await self._out.put(FileChanged(self._seq.next(), call.id, path, "added", sub_session_id=helper))
        elif kind == "subagent" and helper is None:
            await self._out.put(SubAgentFinished(self._seq.next(), self._sub_id(call), call.id, status))
        elif kind == "gateway":
            await self._out.put(
                ToolCallSeen(self._seq.next(), call.id, call.name, status, sub_session_id=helper, arguments=_asked(call))
            )

    async def step_finished(self, reason: Literal["tool-calls", "stop"], usage: ModelUsage) -> None:
        await self._out.put(
            UsageReported(self._seq.next(), usage.input_tokens, usage.output_tokens)
        )

    def _sub_id(self, call: ToolCall) -> str:
        return f"{self._seq.turn_id}:{call.id}"


class _ChildHooks(_Hooks):
    """A sub-agent's steps are named as its own (EV-07); its text is the tool result the parent sees."""

    def __init__(self, session: FakeEngineSession, seq: TurnSeq, out: EventBuffer, sub_id: str) -> None:
        super().__init__(session, seq, out)
        self._helper = sub_id

    @override
    async def text(self, text: str) -> None:
        return None

    @override
    async def step_finished(self, reason: Literal["tool-calls", "stop"], usage: ModelUsage) -> None:
        await self._out.put(
            UsageReported(
                self._seq.next(), usage.input_tokens, usage.output_tokens, sub_session_id=self._helper
            )
        )


ToolKind = Literal["shell", "write", "subagent", "native", "gateway"]


class FakeEngineSession:
    def __init__(
        self,
        *,
        sandbox: FakeSandboxHandle,
        config: FakeEngineConfig,
        headers: Mapping[str, str],
        tool_headers: Mapping[str, str],
        tools: tuple[str, ...],
        history: list[ChatMessage],
        can_steer: bool,
        workdir: str,
        shell: ShellRunner | None = None,
    ) -> None:
        self._sandbox = sandbox
        self._shell = shell
        self._config = config
        self._headers = dict(headers)
        self._tool_headers = dict(tool_headers)
        self.tools = tools
        self.history = history
        self._can_steer = can_steer
        self._workdir = workdir
        self._steering: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._endpoint = ModelEndpoint(config.model_base_url, ENGINE_MODEL_ID, self._headers)

    def tool_kind(self, name: str) -> ToolKind:
        if name == NATIVE_NAMES[NativeTool.SHELL]:
            return "shell"
        if name == NATIVE_NAMES[NativeTool.EDIT]:
            return "write"
        if name in SUBAGENT_TOOLS:
            return "subagent"
        if name in NATIVE_NAMES.values():
            return "native"
        return "gateway"

    async def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        if self._closed:
            raise EngineSessionClosed("session is closed")
        seq = TurnSeq(task.turn_id)
        out = EventBuffer()
        if isinstance(task, EngineTask):
            system = BASE_PROMPT + ("\n" + task.instructions if task.instructions else "")
            self.history.append(ChatMessage("user", task.prompt))
        else:
            system = BASE_PROMPT
            self.history.append(ChatMessage("user", task.text))
        driver = asyncio.get_running_loop().create_task(self._drive(seq, out, system))
        self._task = driver
        try:
            async for event in out.drain():
                yield event
        finally:
            if not driver.done():
                driver.cancel()
            self._task = None

    async def _drive(self, seq: TurnSeq, out: EventBuffer, system: str) -> None:
        try:
            result = await run_agent_loop(
                egress=self._sandbox.egress,
                endpoint=self._endpoint,
                system=system,
                history=self.history,
                tools=self.tools,
                run_tool=lambda call: self._run_tool(call, seq, out, depth=1),
                hooks=_Hooks(self, seq, out),
                max_steps=self._config.max_steps,
                steering=self._steering,
            )
        except asyncio.CancelledError:
            await out.put(TurnCompleted(seq.next(), "interrupted"))
            raise
        except ModelRefused as refused:
            # As opencode and the ACP agents do: the provider's refusal fails the turn, and nothing compacts.
            await out.put(EngineError(seq.next(), refused.code, "the engine reported a turn error"))
            await out.put(TurnCompleted(seq.next(), "failed"))
            return
        await out.put(TurnCompleted(seq.next(), result.reason, result.text))

    async def _run_tool(self, call: ToolCall, seq: TurnSeq, out: EventBuffer, *, depth: int) -> ToolOutcome:
        kind = self.tool_kind(call.name)
        args = call.args()
        if kind == "shell":
            if self._shell is None:
                return ToolOutcome("(no output)")
            return await self._shell(self._sandbox, self._workdir, as_str(args.get("command")))
        if kind == "write":
            path = posixpath.join(self._workdir, as_str(args.get("filePath")))
            self._sandbox.files[path] = as_str(args.get("content")).encode()
            return ToolOutcome("written")
        if kind == "subagent":
            return await self._spawn(call, seq, out, depth=depth)
        if kind == "native":
            return ToolOutcome("")
        return await call_gateway_tool(
            self._sandbox.egress,
            self._config.tools_url,
            self._tool_headers,
            call.name,
            args,
            timeout_s=self._config.tool_timeout_s,
        )

    async def _spawn(self, call: ToolCall, seq: TurnSeq, out: EventBuffer, *, depth: int) -> ToolOutcome:
        if not self._config.sub_agents_enabled or depth > self._config.sub_agent_depth:
            return ToolOutcome("sub-agents are not allowed here", is_error=True)
        child_tools = tuple(
            t for t in self.tools if t not in SUBAGENT_TOOLS or depth < self._config.sub_agent_depth
        )
        sub_id = f"{seq.turn_id}:{call.id}"
        child_history = [ChatMessage("user", as_str(call.args().get("prompt")))]
        result = await run_agent_loop(
            egress=self._sandbox.egress,
            endpoint=self._endpoint,
            system=BASE_PROMPT,
            history=child_history,
            tools=child_tools,
            run_tool=lambda inner: self._run_tool(inner, seq, out, depth=depth + 1),
            hooks=_ChildHooks(self, seq, out, sub_id),
            max_steps=self._config.max_steps,
        )
        return ToolOutcome(result.text)

    async def steer(self, text: str) -> None:
        if not self._can_steer:
            raise SteeringUnsupported("this engine does not accept steering")
        await self._steering.put(text)

    async def interrupt(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def compact(self, focus: str | None, *, instructions: str = "") -> CompactReport:
        """One summarising model call over the history, which the summary then replaces (as Claude Code's does)."""
        if self._closed:
            raise EngineSessionClosed("session is closed")
        before = _history_tokens(self.history)
        prompt = COMPACT_PROMPT + (f"\nKeep in particular: {focus}" if focus else "")
        reply = await call_model(self._sandbox.egress, self._endpoint, [*self.history, ChatMessage("user", prompt)], ())
        if not reply.content:
            raise CompactFailed("the model returned no summary")
        self.history[:] = [ChatMessage("user", SUMMARY_PREFIX + reply.content), ChatMessage("assistant", "Noted.")]
        return CompactReport("compacted", before, _history_tokens(self.history))

    async def answer(self, request_id: str, decision: InteractionDecision) -> None:
        raise LookupError(f"no pending engine request {request_id}")

    async def capture_state(self) -> EngineState:
        payload = json.dumps({"history": [m.to_json() for m in self.history]}, sort_keys=True)
        return EngineState(self._config.engine, STATE_SCHEMA, payload.encode())

    async def health(self) -> SessionHealth:
        return SessionHealth(alive=not self._closed, engine_version=self._config.engine.version)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.interrupt()


def _asked(call: ToolCall) -> dict[str, JsonValue] | None:
    """The call's arguments for its step, None when the script's are not a JSON object (a malformed call is the
    gateway's to refuse, never the fake engine's reason to stop)."""
    try:
        return call.args()
    except ValueError:
        return None


__all__ = [
    "BASE_PROMPT",
    "FAKE_VERSION",
    "NATIVE_NAMES",
    "FakeEngineAdapter",
    "FakeEngineConfig",
    "FakeEngineSession",
]

