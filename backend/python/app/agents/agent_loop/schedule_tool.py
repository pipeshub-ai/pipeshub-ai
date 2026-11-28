"""Classic chat's ``schedule_create`` (ADR-SCH-02 D5): the harness tool's arguments, checks and words
(``app.schedules.arguments``), asked for on classic chat's approval card every time.

The call that makes the schedule runs inside the person's own request that approved it (classic chat's resume),
so it creates through Node's ordinary schedules API with that request's own Authorization: Node makes it with every
check the Schedules page makes, and nothing here holds or mints a credential.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any, Final, Literal, cast, override
from urllib.parse import quote

import httpx

from app.agents.agent_loop.tool_approvals import (
    PLATFORM_TOOLS_BY_PATH,
    ApprovalTarget,
    watched,
)
from app.api.middlewares.caller_role import nodejs_endpoint
from app.harness.journal_node.client import error_code, error_message
from app.schedules.arguments import (
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
    TOOL_IDS,
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
)
from app.schedules.toolset import register_schedules_toolset
from app.services.featureflag.platform_settings import is_scheduled_runs_on
from app.utils.logging_fields import log_event
from harness_core.tools.base import Tag, Tool, ToolOutput, ToolParameter
from harness_core.tools.tags import EFFECT_TAG_KEY, TAG_SINK_SELF

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from app.agent_loop_lib.tools.registry import ToolRegistry
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

SCHEDULES_API_PATH: Final = "/api/v1/agent-harness/schedules"
SCHEDULE_TOOL_PATH: Final = "/platform/schedules/create"
SCHEDULE_UPDATE_PATH: Final = "/platform/schedules/update"
SCHEDULE_DELETE_PATH: Final = "/platform/schedules/delete"
_TIMEOUT_SECONDS: Final = 10.0


def create_body(
    arguments: Mapping[str, Any], *, agent_key: str | None, chat_mode: object, model_key: object, model_name: object,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """The schedules API's create body for an approved call: each run asks ``ask`` of the chat's own agent, model
    and mode, so a schedule made in a chat runs as that chat would."""
    when = arguments["when"]
    trigger: dict[str, Any] = (
        {"kind": "once", "at": when["at"], "timeZone": when["timeZone"]}
        if when["kind"] == "once"
        else {"kind": "cron", "cron": when["cron"], "timeZone": when["timeZone"]}
    )
    chosen = (("chatMode", chat_mode), ("modelKey", model_key), ("modelName", model_name))
    template: dict[str, Any] = {
        "query": arguments["ask"], **{field: value for field, value in chosen if isinstance(value, str) and value},
    }
    body: dict[str, Any] = {"name": arguments["name"], "input": template, "trigger": trigger}
    if agent_key:
        body["agentKey"] = agent_key
    if "notify" in arguments:
        body["notify"] = list(arguments["notify"])
    return {**body, **_from_conversation(conversation_id)}


def _from_conversation(conversation_id: str | None) -> dict[str, Any]:
    """The chat a create or a change was asked in, which Node records (its origin, or where it was last changed)."""
    return {"fromConversation": {"conversationId": conversation_id}} if conversation_id else {}


def update_request(arguments: Mapping[str, Any], conversation_id: str | None) -> tuple[str, str, dict[str, Any] | None]:
    """``(method, path suffix, body)`` of the schedules API for an approved ``schedule_update``: a field change is one
    PATCH, an action its own route. Run now takes no revision; the tool reads it first."""
    action = arguments.get("action")
    if action in ("pause", "resume"):
        return "POST", f"/{action}", {"revision": arguments["revision"], **_from_conversation(conversation_id)}
    if action == "skip_next":
        return "POST", "/skip", {"revision": arguments["revision"], **_from_conversation(conversation_id)}
    if action == "run_now":
        return "POST", "/run", None
    body: dict[str, Any] = {"revision": arguments["revision"]}
    if "name" in arguments:
        body["name"] = arguments["name"]
    if "ask" in arguments:
        body["input"] = {"query": arguments["ask"]}
    if "when" in arguments:
        body["trigger"] = dict(arguments["when"])
    if "notify" in arguments:
        body["notify"] = list(arguments["notify"])
    return "PATCH", "", {**body, **_from_conversation(conversation_id)}


class SchedulesApi:
    """Node's public schedules API (``/api/v1/agent-harness/schedules``), as the person whose request this is."""

    def __init__(self, config_service: Any, *, transport: httpx.AsyncBaseTransport | None = None) -> None:  # noqa: ANN401
        self._config_service = config_service
        self._transport = transport

    async def create(self, body: dict[str, Any], *, authorization: str, locale: str | None) -> ScheduleMade:
        made = schedule_made(await self.request("POST", "", authorization=authorization, locale=locale, body=body))
        log_event(logger, "schedule.created", schedule_id=made.schedule_id)
        return made

    async def find(self, params: dict[str, str], *, authorization: str) -> list[dict[str, Any]]:
        answer = await self.request("GET", "", authorization=authorization, params=params)
        schedules = answer.get("schedules") if isinstance(answer, dict) else None
        if not isinstance(schedules, list):
            raise ScheduleUnavailable("invalid schedule list from Node")
        return [cast("dict[str, Any]", v) for v in cast("list[object]", schedules) if isinstance(v, dict)]

    async def read(self, schedule_id: str, *, authorization: str) -> dict[str, Any]:
        answer = await self.request("GET", f"/{quote(schedule_id, safe='')}", authorization=authorization)
        if not isinstance(answer, dict):
            raise ScheduleUnavailable("invalid schedule answer from Node")
        return answer

    async def request(
        self, method: str, suffix: str, *, authorization: str, locale: str | None = None,
        body: dict[str, Any] | None = None, params: dict[str, str] | None = None, headers: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        """One call as the person; their answer's JSON (``None`` for no content). A 4xx raises ``ScheduleRefused``,
        anything else that is not a success ``ScheduleUnavailable``."""
        sent = {"authorization": authorization, **(headers or {})}
        if locale:
            # Node pins the person's language for the schedule's runs from it, as from the Schedules page.
            sent["accept-language"] = locale
        url = f"{await nodejs_endpoint(self._config_service)}{SCHEDULES_API_PATH}{suffix}"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, transport=self._transport) as client:
                response = await client.request(method, url, json=body, params=params, headers=sent)
        except httpx.HTTPError as exc:
            log_event(logger, "schedule.unavailable", level=logging.WARNING, error_class=type(exc).__name__)
            raise ScheduleUnavailable("Node unreachable") from exc
        if 200 <= response.status_code < 300:
            if response.status_code == 204 or not response.content:
                return None
            try:
                data: object = response.json()
            except ValueError as exc:
                raise ScheduleUnavailable("invalid schedule answer from Node") from exc
            return cast("dict[str, Any]", data) if isinstance(data, dict) else None
        code = error_code(response)
        if 400 <= response.status_code < 500:
            log_event(logger, "schedule.refused", level=logging.WARNING, status=response.status_code, detail=code or None)
            raise ScheduleRefused(code, error_message(response))
        log_event(logger, "schedule.unavailable", level=logging.WARNING, status=response.status_code, detail=code or None)
        raise ScheduleUnavailable(f"schedules API answered {response.status_code}")


class _ClassicScheduleTool(Tool):
    """What classic chat's schedule tools share: the contract, the words, and Node as the request's person."""

    _name: str
    _contract: Literal["create", "update", "delete", "find"]
    _short: str
    _long: str
    _effect: str
    _label: str | None = None

    def __init__(self, context: AgentContext, api: SchedulesApi) -> None:
        self._context = context
        self._api = api

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
        return f"/platform/schedules/{self._contract}"

    @property
    @override
    def display_name(self) -> str | None:
        return self._label

    @property
    @override
    def tags(self) -> list[Tag]:
        # A schedule is the person's own, read by no one else: its arguments stay with them.
        return [Tag(key=EFFECT_TAG_KEY, value=self._effect), TAG_SINK_SELF]

    @property
    @override
    def parameters(self) -> list[ToolParameter]:
        return contract_parameters(self._contract)

    @property
    @override
    def raw_input_schema(self) -> dict[str, Any] | None:
        return contract_schema(self._contract)

    @override
    def validate(self, kwargs: dict[str, Any]) -> None:
        # The person approves the call as made: no default filled in, no enum corrected. ``execute`` checks it.
        del kwargs


class ClassicScheduleCreateTool(_ClassicScheduleTool):
    _name, _contract, _short, _long, _effect, _label = (
        SCHEDULE_CREATE_TOOL, "create", SHORT_DESCRIPTION, DESCRIPTION, "write", CARD_LABEL,
    )

    @override
    async def execute(self, **kwargs: Any) -> ToolOutput:
        # Without ``now``: a one-off approved after its time is still made and fires on the next tick (ADR-SCH-01 D3).
        problem = schedule_problem(kwargs)
        if problem is not None:
            return ToolOutput(success=False, error=invalid_text(problem), error_code="invalid_call")
        context = self._context
        authorization = context.node_authorization()
        if authorization is None:
            return ToolOutput(success=False, error=unavailable_text(), error_code="unavailable")
        state = context.tool_state
        body = create_body(
            kwargs, agent_key=context.agent_key, chat_mode=state.get("chat_mode"),
            model_key=state.get("model_key"), model_name=state.get("model_name") or context.model_name,
            conversation_id=context.conversation_id,
        )
        try:
            made = await self._api.create(body, authorization=authorization, locale=context.locale)
        except ScheduleRefused as refused:
            return ToolOutput(success=False, error=refused_text(refused), error_code="failed")
        except ScheduleUnavailable:
            return ToolOutput(success=False, error=unavailable_text(), error_code="unavailable")
        return ToolOutput(
            success=True,
            data={"scheduleId": made.schedule_id, "result": scheduled_text(made, kwargs, zone=context.timezone)},
        )


class ClassicScheduleFindTool(_ClassicScheduleTool):
    _name, _contract, _short, _long, _effect = (
        SCHEDULE_FIND_TOOL, "find", FIND_SHORT_DESCRIPTION, FIND_DESCRIPTION, "read",
    )

    @override
    async def execute(self, **kwargs: Any) -> ToolOutput:
        problem = find_problem(kwargs)
        if problem is not None:
            return ToolOutput(success=False, error=problem, error_code="invalid_call")
        context = self._context
        authorization = context.node_authorization()
        if authorization is None:
            return ToolOutput(success=False, error="PipesHub's scheduler could not be asked here.", error_code="unavailable")
        params = {"limit": str(FIND_LIMIT)}
        for key in ("query", "status"):
            if isinstance(kwargs.get(key), str):
                params[key] = kwargs[key]
        if isinstance(kwargs.get("madeHere"), bool):
            params["madeHere"] = "true" if kwargs["madeHere"] else "false"
        if context.conversation_id:
            params["conversationId"] = context.conversation_id
        try:
            found = await self._api.find(params, authorization=authorization)
        except ScheduleRefused as refused:
            return ToolOutput(success=False, error=refused_text(refused), error_code="failed")
        except ScheduleUnavailable:
            return ToolOutput(success=False, error="PipesHub's scheduler did not answer; try again in a moment.", error_code="unavailable")
        entries = [
            found_entry(view, zone=context.timezone, conversation_id=context.conversation_id)
            for view in found[:FIND_LIMIT]
        ]
        return ToolOutput(success=True, data={"schedules": entries, "result": found_text(entries)})


class _ClassicScheduleChangeTool(_ClassicScheduleTool):
    @override
    async def execute(self, **kwargs: Any) -> ToolOutput:
        update = self._contract == "update"
        problem = update_problem(kwargs) if update else delete_problem(kwargs)
        if problem is not None:
            return ToolOutput(success=False, error=f"{problem} Nothing was changed.", error_code="invalid_call")
        context = self._context
        authorization = context.node_authorization()
        if authorization is None:
            return ToolOutput(success=False, error=change_unavailable_text(), error_code="unavailable")
        schedule_id = str(kwargs["id"])
        path = f"/{quote(schedule_id, safe='')}"
        try:
            # Read first: the call runs on the revision the person was shown, and run now names none to Node.
            before = await self._api.read(schedule_id, authorization=authorization)
            if before.get("revision") != kwargs["revision"]:
                raise ScheduleRefused("SCHEDULE_CHANGED", "")
            if not update:
                await self._api.request(
                    "DELETE", path, authorization=authorization, params={"revision": str(kwargs["revision"])},
                )
                return _changed(schedule_id, deleted_text(str(before.get("name") or "the schedule")))
            method, suffix, body = update_request(kwargs, context.conversation_id)
            headers = {"idempotency-key": f"chat-{uuid.uuid4().hex}"} if suffix == "/run" else None
            after = await self._api.request(
                method, path + suffix, authorization=authorization, locale=context.locale, body=body, headers=headers,
            )
        except ScheduleRefused as refused:
            return ToolOutput(success=False, error=change_refused_text(refused), error_code="failed")
        except ScheduleUnavailable:
            return ToolOutput(success=False, error=change_unavailable_text(), error_code="unavailable")
        # Run now answers its run, not the schedule: the words come from the schedule as read.
        view = after if isinstance(after, dict) and "id" in after else before
        return _changed(schedule_id, changed_text(view, kwargs, zone=context.timezone))


def _changed(schedule_id: str, text: str) -> ToolOutput:
    return ToolOutput(success=True, data={"scheduleId": schedule_id, "result": text})


class ClassicScheduleUpdateTool(_ClassicScheduleChangeTool):
    _name, _contract, _short, _long, _effect, _label = (
        SCHEDULE_UPDATE_TOOL, "update", UPDATE_SHORT_DESCRIPTION, UPDATE_DESCRIPTION, "write",
        CARD_LABELS[SCHEDULE_UPDATE_TOOL_ID],
    )


class ClassicScheduleDeleteTool(_ClassicScheduleChangeTool):
    _name, _contract, _short, _long, _effect, _label = (
        SCHEDULE_DELETE_TOOL, "delete", DELETE_SHORT_DESCRIPTION, DELETE_DESCRIPTION, "destructive",
        CARD_LABELS[SCHEDULE_DELETE_TOOL_ID],
    )


def _refuses(arguments: dict[str, Any]) -> str | None:
    problem = schedule_problem(arguments, now=utc_now())
    return invalid_text(problem) if problem is not None else None


def _refuses_change(tool: str) -> Callable[[dict[str, Any]], str | None]:
    def refuses(arguments: dict[str, Any]) -> str | None:
        problem = update_problem(arguments, now=utc_now()) if tool == "update" else delete_problem(arguments)
        return f"{problem} Nothing was changed." if problem is not None else None

    return refuses


def _reads(context: AgentContext, api: SchedulesApi) -> Callable[[dict[str, Any]], Awaitable[tuple[dict[str, Any] | None, str | None]]]:
    """The schedule a change's card shows, as it is now (ADR-SCH-03 D5); a schedule gone or changed since the model
    read it is refused without a card."""

    async def reads(arguments: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        authorization = context.node_authorization()
        if authorization is None:
            return None, change_unavailable_text()
        try:
            view = await api.read(str(arguments["id"]), authorization=authorization)
        except ScheduleRefused as refused:
            return None, change_refused_text(refused)
        except ScheduleUnavailable:
            # Asked without the current values: the card still shows the change the call makes.
            return None, None
        if view.get("revision") != arguments.get("revision"):
            return None, change_refused_text(ScheduleRefused("SCHEDULE_CHANGED", ""))
        return {"schedule": card_schedule(view)}, None

    return reads


def offers_schedule_create(context: AgentContext) -> bool:
    """A person's own chat in the web app, not a turn a schedule fired (a schedule never makes schedules) and not a
    harness run (which has its own, journal-bound tools)."""
    return bool(
        context.schedule_firing_id is None
        and not context.harness_run
        and context.durable_run_id is None
        and watched(context)
        and context.node_authorization() is not None
    )


async def register_schedule_tool(
    registry: ToolRegistry, context: AgentContext, *, api: SchedulesApi | None = None,
) -> bool:
    """Registers the ``schedules`` toolset (create, find, update, delete; ADR-SCH-03 D1) for this request when the
    org has scheduling (ADR-SCH-02 D1) and the turn is a person's chat, with the approval gate's targets for the
    three that change something: asked every time, never remembered."""
    if not offers_schedule_create(context) or not await is_scheduled_runs_on(context.config_service):
        return False
    api = api or SchedulesApi(context.config_service)
    tools: list[_ClassicScheduleTool] = [
        ClassicScheduleCreateTool(context, api),
        ClassicScheduleFindTool(context, api),
        ClassicScheduleUpdateTool(context, api),
        ClassicScheduleDeleteTool(context, api),
    ]
    for tool in tools:
        registry.register_tool(tool)
    register_schedules_toolset(registry, [t.name for t in tools])
    targets = context.tool_state.setdefault(PLATFORM_TOOLS_BY_PATH, {})
    for tool in tools:
        if tool.name == SCHEDULE_FIND_TOOL:
            continue
        create = tool.name == SCHEDULE_CREATE_TOOL
        targets[tool.path] = ApprovalTarget(
            source="platform", instance_id="schedules", tool_name=tool.name, owner="PipesHub",
            kind="destructive" if tool.name == SCHEDULE_DELETE_TOOL else "write", tool=tool,
            title=tool.display_name, tool_id=TOOL_IDS[tool.name],
            refuses=_refuses if create else _refuses_change(tool._contract),  # pyright: ignore[reportPrivateUsage]
            reads=None if create else _reads(context, api),
        )
    return True


__all__ = [
    "SCHEDULES_API_PATH",
    "SCHEDULE_TOOL_ID",
    "ClassicScheduleCreateTool",
    "ClassicScheduleDeleteTool",
    "ClassicScheduleFindTool",
    "ClassicScheduleUpdateTool",
    "SchedulesApi",
    "create_body",
    "offers_schedule_create",
    "register_schedule_tool",
    "update_request",
]
