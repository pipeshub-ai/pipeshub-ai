"""``schedule_create``: a schedule asked for in chat (ADR-SCH-01).

The model proposes the schedule as the person will see it; the person approves that exact call on its own card,
every time (D2: no conversation grant or org rule answers it); Node makes it only on that approval, read from the
run's journal (D3). The worker holds no credential of the person, so the tool posts the approved call to Node's
harness state API with the attempt's token.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal, cast, override
from urllib.parse import quote

from app.harness.journal_node.client import error_code, error_message
from app.schedules.arguments import (
    ASKED_TOOL_IDS,
    CARD_LABEL,
    CARD_LABELS,
    DELETE_DESCRIPTION,
    DELETE_SHORT_DESCRIPTION,
    DESCRIPTION,
    FIND_DESCRIPTION,
    FIND_LIMIT,
    FIND_SHORT_DESCRIPTION,
    SCHEDULE_CREATE_TOOL,
    SCHEDULE_DELETE_TOOL,
    SCHEDULE_DELETE_TOOL_ID,
    SCHEDULE_FIND_TOOL,
    SCHEDULE_TOOL_ID,
    SCHEDULE_UPDATE_TOOL,
    SCHEDULE_UPDATE_TOOL_ID,
    SHORT_DESCRIPTION,
    UPDATE_DESCRIPTION,
    UPDATE_SHORT_DESCRIPTION,
    ScheduleMade,
    ScheduleRefused,
    ScheduleUnavailable,
    card_schedule,
    change_refused_text,
    change_unavailable_text,
    changed_text,
    contract_parameters,
    contract_schema,
    delete_problem,
    deleted_text,
    find_problem,
    found_entry,
    found_text,
    invalid_text,
    refused_text,
    schedule_made,
    schedule_problem,
    scheduled_text,
    unavailable_text,
    update_problem,
    utc_now,
    when_words,
)
from harness_core.contracts import canonical_digest
from harness_core.effects.flow import (
    AskDestination,
    destination_key,
    destination_wire,
    grant_answerable,
)
from harness_core.effects.policy import stricter
from harness_core.effects.redaction import redaction_of
from harness_core.effects.types import (
    POLICY_DENIAL,
    EffectKey,
    ExplainsAsk,
    ExplainsDenial,
    args_digest,
    consult_policy,
    refresh_policy,
)
from harness_core.interactions.hold import approval_interaction_id
from harness_core.journal.types import JournalUnavailable
from harness_core.tools.base import Tag, Tool, ToolOutput, ToolParameter
from harness_core.tools.durable_port import current_tool_context
from harness_core.tools.tags import EFFECT_TAG_KEY, TAG_SINK_SELF

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping
    from datetime import datetime

    import httpx
    from pydantic import JsonValue

    from app.harness.journal_node.client import AttemptRoute
    from harness_core.effects.redaction import Redaction
    from harness_core.effects.types import ApprovalDecision, ApprovalPolicy, ToolEffect
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.tools.registry import ToolRegistry

_PATH: Final = "/schedules"
ASK_RULE: Final = "tool"
ASK_LAYER: Final = "deployment"


# --- Node -----------------------------------------------------------------------------------------------------------


class NodeSchedules:
    """One attempt's schedule creates, through Node's harness state API (``POST /internal/runs/{runId}/schedules``)."""

    def __init__(self, route: AttemptRoute) -> None:
        self._route = route
        self._log = route.log("app.harness.schedules")

    async def create(
        self, interaction_id: str, call_args_digest: str, arguments: Mapping[str, JsonValue],
        called_as: str | None = None,
    ) -> ScheduleMade:
        """``call_args_digest``: the model's call's, which the interaction is keyed on; ``arguments`` may be the
        person's edit of it (AP-06). ``called_as``: the name a ``call_tool`` wrapper named, when the model called
        through it, so Node can tie the wrapper's digest to these arguments. The route adds the attempt's id and
        fence."""
        body: dict[str, JsonValue] = {
            "interactionId": interaction_id,
            "toolId": SCHEDULE_TOOL_ID,
            "callArgsDigest": call_args_digest,
            "arguments": dict(arguments),
            **({"calledAs": called_as} if called_as else {}),
        }
        try:
            response = await self._route.post(_PATH, body=body, operation="schedule.create")
        except JournalUnavailable as exc:
            raise ScheduleUnavailable("Node unreachable") from exc
        if response.status_code in (200, 201):
            return self._made(response)
        code = error_code(response)
        if 400 <= response.status_code < 500:
            self._log.warning("schedule.refused", status=response.status_code, detail=code or None)
            raise ScheduleRefused(code, error_message(response))
        self._log.warning("schedule.unavailable", status=response.status_code, detail=code or None)
        raise ScheduleUnavailable(f"schedule route answered {response.status_code}")

    async def find(self, arguments: Mapping[str, JsonValue]) -> list[dict[str, Any]]:
        """The run's person's own schedules (ADR-SCH-03 D2), this chat's first; Node finds them as that person."""
        params: dict[str, str | int] = {}
        for key in ("query", "status"):
            if isinstance(arguments.get(key), str):
                params[key] = cast("str", arguments[key])
        if isinstance(arguments.get("madeHere"), bool):
            params["madeHere"] = "true" if arguments["madeHere"] else "false"
        response = await self._get(_PATH, params, "schedule.find")
        schedules = _json(response).get("schedules")
        if not isinstance(schedules, list):
            raise ScheduleUnavailable("invalid schedule list from Node")
        return [cast("dict[str, Any]", v) for v in cast("list[object]", schedules) if isinstance(v, dict)]

    async def read(self, schedule_id: str) -> dict[str, Any]:
        """One of the run's person's schedules as it is now: what a change's card shows (ADR-SCH-03 D5)."""
        return _json(await self._get(f"{_PATH}/{quote(schedule_id, safe='')}", {}, "schedule.read"))

    async def change(
        self, kind: str, interaction_id: str, call_args_digest: str, arguments: Mapping[str, JsonValue],
        called_as: str | None = None,
    ) -> dict[str, Any] | None:
        """``kind``: ``update`` or ``delete``, as the person approved it (ADR-SCH-03 D6): Node checks the run's journal
        as for a create. The schedule as it is after the change; ``None`` once deleted."""
        tool_id = SCHEDULE_UPDATE_TOOL_ID if kind == "update" else SCHEDULE_DELETE_TOOL_ID
        body: dict[str, JsonValue] = {
            "interactionId": interaction_id, "toolId": tool_id, "callArgsDigest": call_args_digest,
            "arguments": dict(arguments), **({"calledAs": called_as} if called_as else {}),
        }
        suffix = f"{_PATH}/{quote(str(arguments['id']), safe='')}/{kind}"
        try:
            response = await self._route.post(suffix, body=body, operation=f"schedule.{kind}")
        except JournalUnavailable as exc:
            raise ScheduleUnavailable("Node unreachable") from exc
        self._raise_for(response)
        schedule = _json(response).get("schedule")
        self._log.info(f"schedule.{kind}d", scheduleId=str(arguments["id"]))
        return cast("dict[str, Any]", schedule) if isinstance(schedule, dict) else None

    async def _get(self, suffix: str, params: dict[str, str | int], operation: str) -> httpx.Response:
        try:
            response = await self._route.get(suffix, params=params, operation=operation)
        except JournalUnavailable as exc:
            raise ScheduleUnavailable("Node unreachable") from exc
        self._raise_for(response)
        return response

    def _raise_for(self, response: httpx.Response) -> None:
        if response.status_code in (200, 201):
            return
        code = error_code(response)
        if 400 <= response.status_code < 500:
            self._log.warning("schedule.refused", status=response.status_code, detail=code or None)
            raise ScheduleRefused(code, error_message(response))
        self._log.warning("schedule.unavailable", status=response.status_code, detail=code or None)
        raise ScheduleUnavailable(f"schedule route answered {response.status_code}")

    def _made(self, response: httpx.Response) -> ScheduleMade:
        try:
            data = response.json()
        except ValueError as exc:
            raise ScheduleUnavailable("invalid schedule answer from Node") from exc
        made = schedule_made(data)
        self._log.info("schedule.created", scheduleId=made.schedule_id, created=made.created)
        return made


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        data: object = response.json()
    except ValueError as exc:
        raise ScheduleUnavailable("invalid schedule answer from Node") from exc
    if not isinstance(data, dict):
        raise ScheduleUnavailable("invalid schedule answer from Node")
    return cast("dict[str, Any]", data)


# --- the tool -------------------------------------------------------------------------------------------------------


class ScheduleCreateTool(Tool):
    def __init__(self, schedules: NodeSchedules, *, timezone: str | None = None) -> None:
        self._schedules = schedules
        self._timezone = timezone

    @property
    @override
    def name(self) -> str:
        return SCHEDULE_CREATE_TOOL

    @property
    @override
    def short_description(self) -> str:
        return SHORT_DESCRIPTION

    @property
    @override
    def description(self) -> str:
        return DESCRIPTION

    @property
    @override
    def path(self) -> str:
        return "/toolsets/schedules/create"

    @property
    @override
    def tags(self) -> list[Tag]:
        # The schedule is the person's own, read by no one else: its arguments stay with them.
        return [Tag(key=EFFECT_TAG_KEY, value="write"), TAG_SINK_SELF]

    @property
    @override
    def parameters(self) -> list[ToolParameter]:
        return contract_parameters()

    @property
    @override
    def raw_input_schema(self) -> dict[str, Any] | None:
        return contract_schema()

    @override
    def validate(self, kwargs: dict[str, Any]) -> None:
        # Node binds the approval to the digest of the arguments as called, so they reach it untouched: no default
        # filled in, no enum corrected. ``execute`` checks them instead.
        del kwargs

    @override
    async def execute(self, **kwargs: Any) -> ToolOutput:
        problem = schedule_problem(kwargs)
        if problem is not None:
            return ToolOutput(success=False, error=invalid_text(problem), error_code="invalid_call")
        ctx = current_tool_context()
        if ctx is None:
            return ToolOutput(success=False, error="Schedules can only be made in a PipesHub run.", error_code="unavailable")
        called = ctx.args_digest or args_digest(kwargs)
        interaction = ctx.interaction_id or approval_interaction_id(EffectKey(ctx.run_id, SCHEDULE_TOOL_ID, called))
        try:
            made = await self._schedules.create(interaction, called, kwargs, ctx.called_as)
        except ScheduleRefused as refused:
            return ToolOutput(success=False, error=refused_text(refused), error_code="failed")
        except ScheduleUnavailable:
            return ToolOutput(success=False, error=unavailable_text(), error_code="unavailable")
        return ToolOutput(
            success=True,
            data={"scheduleId": made.schedule_id, "result": scheduled_text(made, kwargs, zone=self._timezone)},
        )


class _ScheduleTool(Tool):
    """What the three tools that manage a schedule share: their contract, their words, and Node."""

    _name: str
    _tool: Literal["update", "delete", "find"]
    _short: str
    _long: str
    _effect: str

    def __init__(self, schedules: NodeSchedules, *, timezone: str | None = None) -> None:
        self._schedules = schedules
        self._timezone = timezone

    @property
    @override
    def name(self) -> str:
        return self._name

    @property
    @override
    def short_description(self) -> str:
        return self._short

    @property
    @override
    def description(self) -> str:
        return self._long

    @property
    @override
    def path(self) -> str:
        return f"/toolsets/schedules/{self._tool}"

    @property
    @override
    def tags(self) -> list[Tag]:
        return [Tag(key=EFFECT_TAG_KEY, value=self._effect), TAG_SINK_SELF]

    @property
    @override
    def parameters(self) -> list[ToolParameter]:
        return contract_parameters(self._tool)

    @property
    @override
    def raw_input_schema(self) -> dict[str, Any] | None:
        return contract_schema(self._tool)

    @override
    def validate(self, kwargs: dict[str, Any]) -> None:
        # As for a create: Node binds the approval to the arguments as called, so they reach it untouched.
        del kwargs


class ScheduleFindTool(_ScheduleTool):
    _name, _tool, _short, _long, _effect = (
        SCHEDULE_FIND_TOOL, "find", FIND_SHORT_DESCRIPTION, FIND_DESCRIPTION, "read",
    )

    @override
    async def execute(self, **kwargs: Any) -> ToolOutput:
        problem = find_problem(kwargs)
        if problem is not None:
            return ToolOutput(success=False, error=problem, error_code="invalid_call")
        try:
            found = await self._schedules.find(kwargs)
        except ScheduleRefused as refused:
            return ToolOutput(success=False, error=refused_text(refused), error_code="failed")
        except ScheduleUnavailable:
            return ToolOutput(success=False, error="PipesHub's scheduler did not answer; try again in a moment.", error_code="unavailable")
        entries = [found_entry(view, zone=self._timezone) for view in found[:FIND_LIMIT]]
        return ToolOutput(success=True, data={"schedules": entries, "result": found_text(entries)})


class _ScheduleChangeTool(_ScheduleTool):
    _kind: str

    @override
    async def execute(self, **kwargs: Any) -> ToolOutput:
        problem = update_problem(kwargs) if self._kind == "update" else delete_problem(kwargs)
        if problem is not None:
            return ToolOutput(success=False, error=f"{problem} Nothing was changed.", error_code="invalid_call")
        ctx = current_tool_context()
        if ctx is None:
            return ToolOutput(success=False, error="Schedules can only be changed in a PipesHub run.", error_code="unavailable")
        tool_id = SCHEDULE_UPDATE_TOOL_ID if self._kind == "update" else SCHEDULE_DELETE_TOOL_ID
        called = ctx.args_digest or args_digest(kwargs)
        interaction = ctx.interaction_id or approval_interaction_id(EffectKey(ctx.run_id, tool_id, called))
        try:
            before = await self._schedules.read(str(kwargs["id"]))
            if before.get("revision") != kwargs["revision"]:
                raise ScheduleRefused("SCHEDULE_CHANGED", "")
            after = await self._schedules.change(self._kind, interaction, called, kwargs, ctx.called_as)
        except ScheduleRefused as refused:
            return ToolOutput(success=False, error=change_refused_text(refused), error_code="failed")
        except ScheduleUnavailable:
            return ToolOutput(success=False, error=change_unavailable_text(), error_code="unavailable")
        if self._kind == "delete":
            text = deleted_text(str(before.get("name") or "the schedule"))
        else:
            text = changed_text(after or before, kwargs, zone=self._timezone)
        return ToolOutput(success=True, data={"scheduleId": str(kwargs["id"]), "result": text})


class ScheduleUpdateTool(_ScheduleChangeTool):
    _name, _tool, _short, _long, _effect, _kind = (
        SCHEDULE_UPDATE_TOOL, "update", UPDATE_SHORT_DESCRIPTION, UPDATE_DESCRIPTION, "write", "update",
    )

    @property
    def reader(self) -> Callable[[str], Awaitable[dict[str, Any]]]:
        return self._schedules.read


class ScheduleDeleteTool(_ScheduleChangeTool):
    _name, _tool, _short, _long, _effect, _kind = (
        SCHEDULE_DELETE_TOOL, "delete", DELETE_SHORT_DESCRIPTION, DELETE_DESCRIPTION, "destructive", "delete",
    )


def schedule_tools(schedules: NodeSchedules, *, timezone: str | None) -> list[Tool]:
    """The ``schedules`` toolset's four tools for one attempt (ADR-SCH-03 D1)."""
    return [
        ScheduleCreateTool(schedules, timezone=timezone),
        ScheduleFindTool(schedules, timezone=timezone),
        ScheduleUpdateTool(schedules, timezone=timezone),
        ScheduleDeleteTool(schedules, timezone=timezone),
    ]


# --- the ask --------------------------------------------------------------------------------------------------------


def schedule_destination(call: ToolCall, tool_id: str = SCHEDULE_TOOL_ID) -> AskDestination:
    """The card's destination: the schedule, keyed by what it is (its arguments), never by a place a grant names."""
    label = CARD_LABELS.get(tool_id, CARD_LABEL)
    return AskDestination(label, destination_key(tool_id, canonical_digest(dict(call.arguments))))


def _problem(tool_id: str, arguments: Mapping[str, Any], now: datetime) -> str | None:
    if tool_id == SCHEDULE_UPDATE_TOOL_ID:
        return update_problem(arguments, now=now)
    if tool_id == SCHEDULE_DELETE_TOOL_ID:
        return delete_problem(arguments)
    return schedule_problem(arguments, now=now)


@dataclass
class ScheduleAsks:
    """``base`` with a person asked before every ``schedule_create``, ``schedule_update`` and ``schedule_delete``
    (ADR-SCH-01 D2, ADR-SCH-03 D5). Unlike the repository asks, no conversation grant and no rule under it can let one
    through: the ask offers no grant and this layer never allows a valid call. A call that cannot succeed runs and is
    refused by the tool, so nobody is asked about it. Before a change is asked about, ``read`` gives the schedule's
    current values for its card; a schedule that is gone or changed since the model read it is not asked about."""

    base: ApprovalPolicy
    now: Callable[[], datetime] = utc_now
    read: Callable[[str], Awaitable[dict[str, Any]]] | None = None
    # By the call's id: the schedule a change's card shows, or why it cannot be changed.
    _current: dict[str, dict[str, Any] | str] = field(default_factory=lambda: {}, init=False, repr=False)

    def _ours(self, effect: ToolEffect) -> bool:
        return effect.tool_id in ASKED_TOOL_IDS

    def __call__(self, call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        decision = self.base(call, effect)
        if not self._ours(effect) or decision == "deny":
            return decision
        if _problem(effect.tool_id, call.arguments, self.now()) is not None or isinstance(self._current.get(call.id), str):
            # The tool refuses it without changing anything: no layer has anything to ask about.
            return "allow" if decision == "ask" else decision
        return stricter(decision, "ask")

    async def refresh(self) -> None:
        await refresh_policy(self.base)

    async def consult(self, call: ToolCall, effect: ToolEffect) -> None:
        await consult_policy(self.base, call, effect)
        if (
            self.read is None
            or effect.tool_id not in (SCHEDULE_UPDATE_TOOL_ID, SCHEDULE_DELETE_TOOL_ID)
            or _problem(effect.tool_id, call.arguments, self.now()) is not None
        ):
            return
        try:
            view = await self.read(str(call.arguments["id"]))
        except ScheduleRefused as refused:
            self._current[call.id] = refused.code or "refused"
            return
        except ScheduleUnavailable:
            # Asked without the current values: the card still shows the change the call makes.
            return
        if view.get("revision") != call.arguments.get("revision"):
            self._current[call.id] = "SCHEDULE_CHANGED"
            return
        self._current[call.id] = card_schedule(view)

    def ask_reason(self, call: ToolCall, effect: ToolEffect) -> Mapping[str, JsonValue] | None:
        base = self.base(call, effect)
        reason = self.base.ask_reason(call, effect) if base == "ask" and isinstance(self.base, ExplainsAsk) else None
        if not self._ours(effect) or self(call, effect) != "ask":
            return reason
        where = destination_wire(schedule_destination(call, effect.tool_id))
        # The schedule's current values are not journaled with the reason: `interaction.explained` has no field for
        # them, and a journal refuses the whole commit for an unknown one.
        if reason:
            return {**reason, "conversationGrant": False, "destination": where}
        return {
            "rule": ASK_RULE, "layer": ASK_LAYER, "toolId": effect.tool_id, "conversationGrant": False,
            "destination": where,
        }

    def current(self, call: ToolCall) -> dict[str, Any] | None:
        """The schedule a change's card would show, as read before asking; None when it was not read."""
        current = self._current.get(call.id)
        return current if isinstance(current, dict) else None

    def grant_answers(self, call: ToolCall, effect: ToolEffect) -> bool:
        if self._ours(effect):
            return False
        return self.base(call, effect) != "deny" and grant_answerable(self.base, call, effect)

    def redaction(self, call: ToolCall, effect: ToolEffect) -> Redaction | None:
        return redaction_of(self.base, call, effect)

    def denial(self, call: ToolCall, effect: ToolEffect) -> tuple[str, str]:
        return self.base.denial(call, effect) if isinstance(self.base, ExplainsDenial) else POLICY_DENIAL


def schedule_asks(
    policy: ApprovalPolicy, *, offered: bool, read: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
) -> ApprovalPolicy:
    """The run's policy with the schedule tools that change something asked every time, when the run is offered them;
    ``read``: the run's schedule reader, for a change's card."""
    return ScheduleAsks(policy, read=read) if offered else policy


def schedule_reader(registry: ToolRegistry) -> Callable[[str], Awaitable[dict[str, Any]]] | None:
    """The reader of the schedules the run's ``schedule_update`` reaches, when the run has it."""
    tool = registry.resolve_by_name(SCHEDULE_UPDATE_TOOL) if registry.has(SCHEDULE_UPDATE_TOOL) else None
    return tool.reader if isinstance(tool, ScheduleUpdateTool) else None


__all__ = [
    "SCHEDULE_DELETE_TOOL",
    "SCHEDULE_FIND_TOOL",
    "SCHEDULE_UPDATE_TOOL",
    "ScheduleDeleteTool",
    "ScheduleFindTool",
    "ScheduleUpdateTool",
    "schedule_reader",
    "schedule_tools",
    "CARD_LABEL",
    "SCHEDULE_CREATE_TOOL",
    "SCHEDULE_TOOL_ID",
    "NodeSchedules",
    "ScheduleAsks",
    "ScheduleCreateTool",
    "ScheduleMade",
    "ScheduleRefused",
    "ScheduleUnavailable",
    "schedule_asks",
    "schedule_destination",
    "schedule_problem",
    "when_words",
]
