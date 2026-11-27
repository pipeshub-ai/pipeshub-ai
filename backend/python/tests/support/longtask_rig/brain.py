"""The scripted model of S1: an ``LLMTransport`` that plays a long task from fake-LLM directives.

It sits where the org's model sits in production (``ResolvedModel.transport`` behind the production port), so the
loop's turns and the compaction summaries both reach it, as they reach a real provider. Each user request of the
scenario has a script: a list of steps, each step a block of the fake LLM's directives
(``harness_core.testing.engines.script``: ``tool <name> <json>``, ``tools <name> [<json>, …]``, ``say <text>``,
``usage <n>``).

Two things make it behave like a model rather than a replay:

* It acts on what the request shows. A step is done when the request carries the tool results of the calls it made,
  so a re-drive that re-samples gets the same call again (same call id), and a compaction that summarised the
  earlier calls away does not send it back to the start. If the request it was sent does not hold the user's
  current request, it cannot know what to do, and answers ``LOST_REQUEST`` instead of guessing.
* A call whose result says it never took effect and should be made again (``RETRY_WHEN``: a crash before the
  program ran, a sandbox that was reaped, a sandbox broker that is restarting, a tool that failed transiently) is made
  again with a new call id and no ``sandbox_id``, as a model reading that result would; the step is done once the
  retried call has a final result.
* ``say`` texts may name what only the request can supply: ``{recalled}`` is the remembered notes block the
  harness injected (``NO MEMORY`` when there is none), ``{result:<call id>}`` the first line of that call's result.

Every request is recorded (``ModelRequestRecord``) with what the checks read: whether the latest user request and
the system instructions were in it, the usage reported, and how long the harness took to build it.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from harness_core.kernel.core.messages import (
    AssistantMessage,
    Message,
    TextPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from harness_core.kernel.core.responses import (
    ModelResponse,
    StopReason,
    StructuredResponse,
    TokenUsage,
)
from harness_core.kernel.core.streaming import (
    StreamCompleteEvent,
    StreamEvent,
    TextDeltaEvent,
)
from harness_core.models.transports.base import LLMTransport
from harness_core.testing.engines.script import (
    BYTES_PER_TOKEN,
    ChatMessage,
    ScriptedBrain,
    parse_directives,
    single_calls,
)
from harness_core.testing.engines.script import ToolCall as ScriptCall

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from harness_core.kernel.core.tool_schema import ToolSchema

LOST_REQUEST = "LOST_REQUEST: the request I was sent does not hold what you asked."
# The answer-only turn after a limit (BUDGETEND): the harness's request rides on the newest result's loop footer.
WRAP_UP_MARKER = "[loop: You have reached"
WRAPPED = "STOPPED EARLY"
NO_MEMORY = "NO MEMORY"
SUMMARY_PREFIX = "Summarise the conversation quoted below"
_WIKI_PATH = re.compile(r"wiki/[A-Za-z0-9_./-]+\.md")
_RESULT = re.compile(r"\{result:([A-Za-z0-9_.-]+)\}")
_SANDBOX_ID = re.compile(r'"sandbox_id":\s*"([0-9a-f-]{8,})"')
_REMEMBERED = re.compile(r"<remembered>(.*?)</remembered>", re.S)
# Results that say the call did not take effect and may be made again (each the harness's or a tool's own wording).
RETRY_WHEN: tuple[str, ...] = (
    "run_code_interrupted",
    "start a fresh sandbox by calling run_code with no sandbox_id",
    "sandbox broker unavailable",
    "temporarily unavailable",
)
RATE_LIMITED = "Error code: 429 - Rate limit reached for requests. Please retry after 1 second."


INTENT_TOOL = "task_complete"


def _chat_message(message: Message) -> ChatMessage:
    if isinstance(message, ToolMessage):
        return ChatMessage("tool", text_of(message), tool_call_id=message.tool_call_id)
    if isinstance(message, AssistantMessage):
        calls = tuple(ScriptCall(c.id, c.name, json.dumps(c.arguments)) for c in message.tool_calls or ())
        return ChatMessage("assistant", message.text, tool_calls=calls)
    return ChatMessage("user", text_of(message))


def digest_of(message: Message) -> str:
    calls = ",".join(c.id for c in getattr(message, "tool_calls", None) or ())
    body = f"{text_of(message)}|{getattr(message, 'tool_call_id', '') or ''}|{calls}"
    return f"{message.role.value}:{hashlib.sha256(body.encode()).hexdigest()[:16]}"


def text_of(message: Message) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return "".join(getattr(part, "text", "") for part in content)


@dataclass(frozen=True)
class Step:
    """One model response: its directives, and the call ids it uses (``<task>-<n>[-<i>]``)."""

    directives: str
    call_ids: tuple[str, ...]

    @property
    def is_answer(self) -> bool:
        return not self.call_ids


def compile_steps(task: str, blocks: list[str]) -> list[Step]:
    """Each block's calls get ids from the task's label and the block's place, so a re-sample reuses them."""
    steps: list[Step] = []
    for n, block in enumerate(blocks, start=1):
        calls = [c for d in parse_directives(block) if d.verb in ("tool", "tools") for c in single_calls(d)]
        ids = tuple(f"{task}-{n}" if len(calls) == 1 else f"{task}-{n}-{i}" for i in range(len(calls)))
        steps.append(Step(block, ids if calls else ()))
    if not steps or not steps[-1].is_answer:
        raise ValueError(f"{task}: a script ends with a say step")
    return steps


@dataclass
class ModelRequestRecord:
    index: int
    task: str | None
    kind: str  # "turn" | "intent" | "delegate" | "summary" | "other"
    tools: tuple[str, ...]
    has_latest_request: bool
    has_system: bool
    reported_input_tokens: int
    estimated_tokens: int
    reply_calls: tuple[str, ...]
    reply_text: str
    tool_results: dict[str, str]
    recalled: str | None
    at: float
    user_texts: tuple[str, ...] = ()
    # The journal's last seq when the request went out, and the history it carried (role and digest of each
    # message the harness did not inject), so a cut in history can be matched to a journaled reduction.
    journal_seq: int = 0
    history: tuple[str, ...] = ()
    refused: bool = False
    # The digest of the whole tools array the request carried (names, descriptions, schemas, order): R4.
    tools_digest: str = ""


def tools_digest(tools: list[ToolSchema] | None) -> str:
    """One digest of a request's tools array as a provider caches it: every field, in the order sent."""
    listed = [[t.name, t.description, t.input_schema] for t in tools or ()]
    return hashlib.sha256(json.dumps(listed, sort_keys=True).encode()).hexdigest()[:16]


@dataclass
class LongTaskModel(LLMTransport):
    """The fake model. ``scripts``: task label → (the user request it answers, its steps)."""

    scripts: dict[str, tuple[str, list[Step]]]
    system_marker: str
    requests: list[ModelRequestRecord] = field(default_factory=list)
    active: str | None = None
    seen: set[str] = field(default_factory=set)
    results: dict[str, str] = field(default_factory=dict)
    _model: str = "longtask-scripted"
    _delegates: ScriptedBrain = field(default_factory=ScriptedBrain)
    # The tier the intent step names for ``auto`` (``quick``: the run's own loop holds every tool).
    route: str | None = None
    # A task whose intent step answers with this text instead (a ```clarify block: the run asks the person first).
    intents: dict[str, str] = field(default_factory=dict)
    journal_seq: Callable[[], int] = lambda: 0
    refusals: set[str] = field(default_factory=set)
    arm_crash: Callable[[str], None] = lambda point: None
    # Each history digest's text (its start), so a cut in history can be shown.
    texts: dict[str, str] = field(default_factory=dict)
    # Sandboxes this model may reuse, as the tool asks it to within a turn: task -> (worker epoch, sandbox id).
    # A crash raises the epoch: the dead worker's sandboxes are gone.
    epoch: int = 0
    reuse_sandboxes: bool = True
    sandboxes: dict[str, tuple[int, str]] = field(default_factory=dict)
    _issued_epoch: dict[str, int] = field(default_factory=dict)
    _tools_digest: str = ""
    # A script's call id -> the id it was last made under (a retried call gets ``<id>-r<n>``).
    alias: dict[str, str] = field(default_factory=dict)
    # Call ids whose result said to make the call again.
    retryable: set[str] = field(default_factory=set)
    retry_when: tuple[str, ...] = RETRY_WHEN
    # ``hook <name>`` in a step: what happens in the world once the model has answered it, before the harness acts
    # on the answer (S2: the reaper ends the request's sandboxes, the sandbox broker restarts).
    hooks: dict[str, Callable[[], Awaitable[None]]] = field(default_factory=dict)
    _due: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__init__()

    @property
    def provider(self) -> str:
        return "openai"

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        *,
        tool_choice: object = None,
    ) -> ModelResponse:
        response = self._respond(messages, tools, system)
        await self._run_hooks()
        return response

    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict[str, Any],
        system: str | None = None,
        model: str | None = None,
    ) -> StructuredResponse:
        self._record("other", messages, (), system, None, (), "")
        return StructuredResponse(data={}, usage=TokenUsage(input_tokens=1, output_tokens=1), model=self._model)

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        *,
        tool_choice: object = None,
    ) -> AsyncIterator[StreamEvent]:
        response = self._respond(messages, tools, system)
        await self._run_hooks()
        text = response.message.text
        if text:
            yield TextDeltaEvent(delta=text)
        yield StreamCompleteEvent(response=response)

    async def _run_hooks(self) -> None:
        due, self._due = self._due, []
        for name in due:
            await self.hooks[name]()

    # --- what the model decides -------------------------------------------------------------------------------

    def _respond(self, messages: list[Message], tools: list[ToolSchema] | None, system: str | None) -> ModelResponse:
        offered = tuple(t.name for t in tools or ())
        self._tools_digest = tools_digest(tools)
        first = text_of(messages[0]) if messages else ""
        if not offered and first.startswith(SUMMARY_PREFIX):
            return self._summary(messages, system, first)
        if offered == (INTENT_TOOL,):
            return self._intent(messages, system, offered)
        if not offered or self.active is None:
            return self._plain(messages, system, offered)
        if any(parse_directives(text_of(m)) for m in messages if isinstance(m, UserMessage)):
            return self._delegate(messages, system, offered)
        return self._turn(messages, system, offered)

    def _intent(self, messages: list[Message], system: str | None, offered: tuple[str, ...]) -> ModelResponse:
        """The intent step's restatement: a faithful one, the person's request kept word for word."""
        asked = text_of(messages[-1]).split(":", 1)[-1].strip() if messages else ""
        text = self.intents.get(self.active or "") or f"## Restatement\n{asked}" + (f"\n{self.route}" if self.route else "")
        self._record("intent", messages, offered, system, None, (), text)
        return ModelResponse(message=AssistantMessage(content=[TextPart(text=text)]), usage=TokenUsage(input_tokens=self._estimate(messages, system), output_tokens=len(text) // BYTES_PER_TOKEN), model=self._model)

    def _delegate(self, messages: list[Message], system: str | None, offered: tuple[str, ...]) -> ModelResponse:
        """A delegated agent (``coding_agent``): its goal is directives the parent wrote, played as the fake LLM
        plays an engine's prompt."""
        chat = [_chat_message(m) for m in messages]
        reply = self._delegates.reply(chat, list(offered), f"d{len(self.requests)}")
        calls = tuple(ToolCall(id=c.id, name=c.name, arguments=json.loads(c.arguments or "{}")) for c in reply.tool_calls)
        self._record("delegate", messages, offered, system, None, tuple(c.id for c in calls), reply.content)
        message = AssistantMessage(content=[TextPart(text=reply.content)] if reply.content else [], tool_calls=list(calls) or None)
        return ModelResponse(
            message=message,
            usage=TokenUsage(input_tokens=self._estimate(messages, system), output_tokens=reply.usage.output_tokens),
            stop_reason=StopReason.TOOL_USE if calls else StopReason.END_TURN,
            model=self._model,
        )

    def _summary(self, messages: list[Message], system: str | None, prompt: str) -> ModelResponse:
        """A summary that keeps what the work needs: the goal and the files it touched."""
        quoted = prompt.split("\n", 1)[-1]
        files = sorted(set(_WIKI_PATH.findall(quoted)))
        text = (
            f"Goal: the long task ({self.active or 'unknown'}). Decisions made: keep notes in the wiki. "
            f"Files, records and artifacts touched: {', '.join(files) or 'none'}. Next step: continue the plan."
        )
        self._record("summary", messages, (), system, None, (), text)
        return ModelResponse(
            message=AssistantMessage(content=[TextPart(text=text)]),
            usage=TokenUsage(input_tokens=self._estimate(messages, system), output_tokens=len(text) // BYTES_PER_TOKEN),
            model=self._model,
        )

    def _plain(self, messages: list[Message], system: str | None, offered: tuple[str, ...]) -> ModelResponse:
        self._record("other", messages, offered, system, None, (), "ok")
        return ModelResponse(message=AssistantMessage(content=[TextPart(text="ok")]), usage=TokenUsage(input_tokens=1, output_tokens=1), model=self._model)

    def _turn(self, messages: list[Message], system: str | None, offered: tuple[str, ...]) -> ModelResponse:
        assert self.active is not None
        request, steps = self.scripts[self.active]
        for message in messages:
            if isinstance(message, ToolMessage) and message.tool_call_id:
                content = text_of(message)
                if any(marker in content for marker in self.retry_when):
                    self.retryable.add(message.tool_call_id)
                    self.sandboxes.pop(self.active, None)
                    continue
                self.seen.add(message.tool_call_id)
                self.results.setdefault(message.tool_call_id, content)
                self._note_sandbox(message.tool_call_id, content)
        users = [text_of(m) for m in messages if isinstance(m, UserMessage)]
        has_request = any(request in u for u in users)
        recalled = recalled_of(messages)
        if not has_request:
            return self._reply(messages, system, offered, recalled, (), LOST_REQUEST, None)
        if any(WRAP_UP_MARKER in text_of(m) + str(getattr(m, "step_footer", "") or "") for m in messages):
            # Told to answer now with what it has: it does, and says it stopped early, as a model would.
            done = sorted(self._current(i) for s in steps for i in s.call_ids if self._current(i) in self.seen)
            said = f"{WRAPPED} ({self.active}): what I found so far is in the wiki; {len(done)} steps done."
            return self._reply(messages, system, offered, recalled, (), said, None)
        step = next((s for s in steps if s.is_answer or not {self._current(i) for i in s.call_ids} <= self.seen), steps[-1])
        refusal = next((line.strip()[len("refuse "):] for line in step.directives.splitlines() if line.strip().startswith("refuse ")), None)
        key = f"{self.active}:{steps.index(step)}"
        if refusal is not None and key not in self.refusals:
            self.refusals.add(key)
            self._record("turn", messages, offered, system, recalled, (), "", refused=True)
            raise RuntimeError(f"Error code: 400 - This model's {refusal}")
        if "ratelimit" in _verbs(step) and f"429:{key}" not in self.refusals:
            self.refusals.add(f"429:{key}")
            self._record("turn", messages, offered, system, recalled, (), "", refused=True)
            raise RuntimeError(RATE_LIMITED)
        for line in _verbs(step):
            if line.startswith("hook ") and f"{line}:{key}" not in self.refusals:
                self.refusals.add(f"{line}:{key}")
                self._due.append(line[len("hook "):])
        crash = next((line.strip()[len("crash "):] for line in step.directives.splitlines() if line.strip().startswith("crash ")), None)
        if crash is not None and f"crash:{key}" not in self.refusals:
            self.refusals.add(f"crash:{key}")
            self.arm_crash(crash)
        directives = parse_directives(step.directives)
        usage = next((int(d.arg) for d in reversed(directives) if d.verb == "usage" and d.arg.isdigit()), None)
        if step.is_answer:
            said = " ".join(d.arg for d in directives if d.verb == "say")
            return self._reply(messages, system, offered, recalled, (), self._fill(said, recalled), usage)
        calls = [c for d in directives if d.verb in ("tool", "tools") for c in single_calls(d)]
        made: list[ToolCall] = []
        for script_id, directive in zip(step.call_ids, calls, strict=True):
            call_id = self._issue(script_id)
            name, _, raw = directive.arg.partition(" ")
            exposed = next((t for t in offered if t == name or t.endswith(("_" + name, "." + name))), None)
            arguments = json.loads(raw or "{}")
            if exposed is None and "call_tool" in offered:
                # A connector tool the chat's fixed list does not hold: called through ``call_tool`` under the name
                # ``search_tools`` answered (R4, SMOKEFIX-1), as the product tells a model to.
                found = _found_name(messages, name)
                if found is not None:
                    exposed, arguments = "call_tool", {"name": found, "arguments": arguments}
            if exposed is None:
                return self._reply(messages, system, offered, recalled, (), f"tool unavailable: {name}", usage)
            reuse = self.sandboxes.get(self.active)
            if self.reuse_sandboxes and exposed == "run_code" and reuse is not None and reuse[0] == self.epoch:
                arguments.setdefault("sandbox_id", reuse[1])
            self._issued_epoch[call_id] = self.epoch
            made.append(ToolCall(id=call_id, name=exposed, arguments=arguments))
        return self._reply(messages, system, offered, recalled, tuple(made), "", usage)

    def _current(self, script_id: str) -> str:
        return self.alias.get(script_id, script_id)

    def _issue(self, script_id: str) -> str:
        """The id to make a script's call under: a new one once its last result said to make it again."""
        current = self._current(script_id)
        if current in self.retryable:
            n = int(current.rsplit("-r", 1)[1]) + 1 if current != script_id else 1
            current = self.alias[script_id] = f"{script_id}-r{n}"
        return current

    def _note_sandbox(self, call_id: str, content: str) -> None:
        task = call_id.rsplit("-", 1)[0].split("-")[0]
        found = _SANDBOX_ID.search(content)
        if found and call_id in self._issued_epoch:
            self.sandboxes[task] = (self._issued_epoch[call_id], found.group(1))

    def _fill(self, text: str, recalled: str | None) -> str:
        text = text.replace("{recalled}", recalled or NO_MEMORY)
        return _RESULT.sub(lambda m: (self.results.get(self._current(m.group(1)), "NO RESULT").strip().splitlines() or [""])[0], text)

    def _reply(
        self,
        messages: list[Message],
        system: str | None,
        offered: tuple[str, ...],
        recalled: str | None,
        calls: tuple[ToolCall, ...],
        text: str,
        usage: int | None,
    ) -> ModelResponse:
        estimated = self._estimate(messages, system)
        self._record("turn", messages, offered, system, recalled, tuple(c.id for c in calls), text, usage or estimated)
        message = AssistantMessage(content=[TextPart(text=text)] if text else [], tool_calls=list(calls) or None)
        written = len(text) + sum(len(json.dumps(c.arguments)) for c in calls)
        return ModelResponse(
            message=message,
            usage=TokenUsage(input_tokens=usage or estimated, output_tokens=max(1, written // BYTES_PER_TOKEN)),
            stop_reason=StopReason.TOOL_USE if calls else StopReason.END_TURN,
            model=self._model,
        )

    def _remember(self, message: Message) -> str:
        digest = digest_of(message)
        self.texts.setdefault(digest, text_of(message)[:300])
        return digest

    # --- what is recorded -------------------------------------------------------------------------------------

    @staticmethod
    def _estimate(messages: list[Message], system: str | None) -> int:
        size = len(system or "") + sum(len(text_of(m)) for m in messages)
        return max(1, size // BYTES_PER_TOKEN)

    def _record(
        self,
        kind: str,
        messages: list[Message],
        offered: tuple[str, ...],
        system: str | None,
        recalled: str | None,
        calls: tuple[str, ...],
        text: str,
        reported: int | None = None,
        *,
        refused: bool = False,
    ) -> None:
        request = self.scripts[self.active][0] if self.active in self.scripts else None
        self.requests.append(
            record_of(
                len(self.requests), self.active, request, kind, messages, offered, system, self.system_marker,
                recalled=recalled, calls=calls, text=text, reported=reported or 0,
                journal_seq=self.journal_seq(), remember=self._remember, refused=refused,
                tools_digest=self._tools_digest if offered else "",
            )
        )


_ANSWERED_NAME = re.compile(r'"name"\s*:\s*"([^"]+)"')


def _found_name(messages: list[Message], name: str) -> str | None:
    for message in messages:
        if isinstance(message, ToolMessage):
            found = next((n for n in _ANSWERED_NAME.findall(text_of(message)) if n == name or n.endswith("_" + name)), None)
            if found is not None:
                return found
    return None


def _verbs(step: Step) -> set[str]:
    return {line.strip() for line in step.directives.splitlines()}


def recalled_of(messages: list[Message]) -> str | None:
    """The remembered notes the harness gave the model, if any."""
    users = [text_of(m) for m in messages if isinstance(m, UserMessage)]
    return next((m.group(1).strip() for u in users for m in [_REMEMBERED.search(u)] if m), None)


def kind_of(messages: list[Message], offered: tuple[str, ...], system: str | None, marker: str) -> str:
    """What a request is: the loop's ``turn``, the ``intent`` step, a ``summary`` for a compaction, a delegate's
    turn (its own system prompt), or ``other``."""
    first = text_of(messages[0]) if messages else ""
    if not offered and first.startswith(SUMMARY_PREFIX):
        return "summary"
    if offered == (INTENT_TOOL,):
        return "intent"
    if not offered:
        return "other"
    return "turn" if marker in (system or "") else "delegate"


def record_of(
    index: int,
    task: str | None,
    request: str | None,
    kind: str,
    messages: list[Message],
    offered: tuple[str, ...],
    system: str | None,
    marker: str,
    *,
    recalled: str | None,
    calls: tuple[str, ...],
    text: str,
    reported: int,
    journal_seq: int,
    remember: Callable[[Message], str] = digest_of,
    refused: bool = False,
    tools_digest: str = "",
) -> ModelRequestRecord:
    """One request as the checks read it, whoever answered it (the scripted model or a live one)."""
    users = tuple(text_of(m) for m in messages if isinstance(m, UserMessage))
    return ModelRequestRecord(
        index=index,
        task=task,
        kind=kind,
        tools=offered,
        has_latest_request=request is not None and any(request in u for u in users),
        has_system=marker in (system or ""),
        reported_input_tokens=reported,
        estimated_tokens=LongTaskModel._estimate(messages, system),  # pyright: ignore[reportPrivateUsage]
        reply_calls=calls,
        reply_text=text,
        tool_results={m.tool_call_id: text_of(m) for m in messages if isinstance(m, ToolMessage) and m.tool_call_id},
        recalled=recalled,
        at=time.monotonic(),
        user_texts=users,
        journal_seq=journal_seq,
        history=tuple(remember(m) for m in messages if not getattr(m, "injected", False)),
        refused=refused,
        tools_digest=tools_digest,
    )


__all__ = [
    "LOST_REQUEST", "NO_MEMORY", "LongTaskModel", "ModelRequestRecord", "Step", "compile_steps", "kind_of", "record_of",
    "digest_of", "recalled_of", "text_of", "tools_digest",
]
