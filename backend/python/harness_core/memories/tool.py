"""``remember``: a run's model asks to keep a fact or preference (P7.3).

The proposal is the host's to keep or not: it waits until the run ends and is
then accepted, held for review or discarded by the host's policy, so the tool
never says a memory "is saved". It changes nothing outside the harness and is
idempotent per proposal (effect ``workspace``): it never waits for an approval.

One call takes a list, one fact per item, each proposed as its own memory
(FIX-P1-F2): a model told several things at once saved them as one memory, so
the person deleting one of them lost them all. A list lets the model keep one
call per turn and still give each fact its own memory. An item may name the
memory it corrects (``replaces``, FIX-R2 C2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast, override

from harness_core.contracts.generated.policy_data import RELEASE_DEFAULTS
from harness_core.memories.types import (
    MEMORY_KINDS,
    MemoryKind,
    MemoryProposal,
    MemoryRefused,
    MemoryScope,
    MemoryUnavailable,
)
from harness_core.tools.base import ParameterType, Tag, Tool, ToolOutput, ToolParameter
from harness_core.tools.tags import EFFECT_TAG_KEY

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.memories.ports import MemorySource

REMEMBER_TOOL = "remember"
# One call's items; Node keeps at most this many proposals per run anyway (MAX_PROPOSALS_PER_RUN).
MAX_MEMORIES_PER_CALL = 10
_ITEM_FIELDS = ("content", "kind", "scope")
REPLACES_FIELD = "replaces"
# FIX-R2 C2: a correction and the fact it corrected were both recalled and the model hedged between them; a
# correction that names what it replaces retires the old memory once the host accepts it.
REPLACES_HELP = (
    "When the user corrects or updates something in the remembered notes, propose the new fact with `replaces` set "
    "to the id of the memory it corrects, so the old one stops being recalled; never re-propose an unchanged fact."
)

_SCOPE_HELP: dict[MemoryScope, str] = {
    "user": "user: about this user, for their later conversations",
    "session": "session: only for the rest of this conversation, when the user says it applies just here",
    "agent": "agent: about how this agent should work, for everyone who uses it (an admin reviews it first)",
}


class RememberTool(Tool):
    def __init__(self, source: MemorySource, scopes: Sequence[MemoryScope]) -> None:
        self._source = source
        self._scopes = tuple(scopes)

    @property
    @override
    def name(self) -> str:
        return REMEMBER_TOOL

    @property
    @override
    def short_description(self) -> str:
        return "Propose a lasting fact or preference to remember for later conversations."

    @property
    @override
    def description(self) -> str:
        return (
            "Propose durable facts or preferences worth remembering in later conversations, in your own words, only "
            "when the user states them or clearly implies they should persist. Put exactly one fact or preference in "
            f"each item of `memories` (one sentence, at most {RELEASE_DEFAULTS.memory.max_chars} characters): when the "
            "user tells you several things, give each its own item, never one item listing them, because the user "
            "reviews, corrects and deletes memories one at a time and deleting a combined one loses every fact in it. "
            f"{REPLACES_HELP} "
            "Never store secrets, credentials, or content copied from documents. The proposals are kept or reviewed "
            "after this conversation turn ends; do not tell the user they are saved."
        )

    @property
    @override
    def path(self) -> str:
        return f"/toolsets/memory/{REMEMBER_TOOL}"

    @property
    @override
    def tags(self) -> list[Tag]:
        return [Tag(key=EFFECT_TAG_KEY, value="workspace"), Tag(key="idempotent", value="true")]

    @property
    @override
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="memories",
                type=ParameterType.ARRAY,
                description=f"One item per fact or preference (1 to {MAX_MEMORIES_PER_CALL}); never several facts in one item.",
                required=True,
                items={
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "ONE fact or preference, one sentence, in your own words."},
                        "kind": {"type": "string", "description": "fact or preference", "enum": list(MEMORY_KINDS)},
                        "scope": {
                            "type": "string",
                            "description": "; ".join(_SCOPE_HELP[s] for s in self._scopes),
                            "enum": list(self._scopes),
                        },
                        REPLACES_FIELD: {
                            "type": "string",
                            "description": (
                                "Only for a correction or update: the id of the remembered memory this one replaces "
                                "(the id attribute in the remembered notes), in the same scope."
                            ),
                        },
                    },
                    "required": list(_ITEM_FIELDS),
                },
            ),
        ]

    @override
    def validate(self, kwargs: dict[str, Any]) -> None:
        # A call in the shape before the list (one memory per call): journaled by a run that resumes across the
        # upgrade, or sent by a scripted client. It is a list of one.
        if "memories" not in kwargs and set(_ITEM_FIELDS) <= set(kwargs):
            item = {name: kwargs.pop(name) for name in _ITEM_FIELDS}
            kwargs["memories"] = [item]
        super().validate(kwargs)

    @override
    async def execute(self, **kwargs: Any) -> ToolOutput:
        items = kwargs.get("memories")
        problem = _invalid(items)
        if problem is not None:
            return ToolOutput(success=False, error=problem, error_code="invalid_call")
        results: list[dict[str, Any]] = []
        for item in cast("list[dict[str, Any]]", items):
            scope = cast("MemoryScope", item["scope"])
            if scope not in self._scopes:
                results.append(_not_kept(item, "That scope is not available in this conversation."))
                continue
            replaces = item.get(REPLACES_FIELD)
            proposal = MemoryProposal(
                str(item["content"]), cast("MemoryKind", item["kind"]), scope, replaces if replaces else None
            )
            try:
                proposed = await self._source.propose(proposal)
            except MemoryRefused as exc:
                results.append(_not_kept(item, f"Not kept ({exc.detail})."))
                continue
            except MemoryUnavailable:
                return ToolOutput(success=False, error="Memory is unavailable right now; carry on without it.")
            results.append({"memoryId": proposed.memory_id, "status": "proposed for review"})
        if not any("memoryId" in r for r in results):
            return ToolOutput(success=False, error=" ".join(str(r["reason"]) for r in results))
        return ToolOutput(success=True, data={"memories": results})


def _invalid(items: object) -> str | None:
    if not isinstance(items, list) or not items:
        return "`memories` must list at least one memory."
    if len(items) > MAX_MEMORIES_PER_CALL:
        return f"At most {MAX_MEMORIES_PER_CALL} memories in one call."
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not all(isinstance(item.get(name), str) for name in _ITEM_FIELDS):
            return f"memories[{index}] needs content, kind and scope, each a string."
        if item["kind"] not in MEMORY_KINDS:
            return f"memories[{index}].kind must be one of {list(MEMORY_KINDS)}."
        if item.get(REPLACES_FIELD) is not None and not isinstance(item[REPLACES_FIELD], str):
            return f"memories[{index}].{REPLACES_FIELD} must be a memory id."
    return None


def _not_kept(item: dict[str, Any], reason: str) -> dict[str, Any]:
    return {"content": item["content"], "kept": False, "reason": reason}


__all__ = ["MAX_MEMORIES_PER_CALL", "REMEMBER_TOOL", "REPLACES_FIELD", "REPLACES_HELP", "RememberTool"]
