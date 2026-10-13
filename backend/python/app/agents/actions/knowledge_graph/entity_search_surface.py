"""The knowledge-graph tools a request sees when it is offered
``search_by_entities`` (``agent_loop/entity_search_tool``), and the tools it
sees otherwise.

Not offered, the request gets the toolset exactly as declared, minus
``search_by_entities``. Offered, it also gets the descriptions that only hold
with named-entity extraction on (``search_entities`` finds named entities,
``search``/``find_records_by_entity`` take their ids), ``search_entities``
takes several queries at once and explains the kinds it cannot serve, and
whatever names ``search_by_entities`` does so only because it is granted.

Each variant is a small, named edit of the declared text, so the declared tool
stays the single source of its wording; an edit whose anchor is gone fails
loudly instead of silently shipping the old text.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.base import ParameterType, ToolParameter
from app.agent_loop_lib.tools.decorators import TOOL_META_ATTR, BoundMethodTool
from app.agents.agent_loop.entity_search_tool import SEARCH_BY_ENTITIES_TOOL_NAME

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.agent_loop_lib.tools.base import Tool
    from app.agent_loop_lib.tools.decorators import ToolMeta

SEARCH_TOOL_NAME = "knowledgegraph__search"
SEARCH_ENTITIES_TOOL_NAME = "knowledgegraph__search_entities"
FIND_RECORDS_BY_ENTITY_TOOL_NAME = "knowledgegraph__find_records_by_entity"
FIND_RECORDS_BY_VALUE_TOOL_NAME = "knowledgegraph__find_records_by_value"


@dataclass(frozen=True)
class _Edit:
    old: str
    new: str


@dataclass(frozen=True)
class _Variant:
    description: tuple[_Edit, ...] = ()
    parameters: tuple[tuple[str, tuple[_Edit, ...]], ...] = ()
    """``(parameter name, edits of its description)``."""
    method: str | None = None
    """The instance method that serves the variant, when it takes more arguments."""
    extra_parameters: tuple[ToolParameter, ...] = ()
    optional: tuple[str, ...] = ()
    """Parameters the variant no longer requires."""
    args_summary: Callable[[dict[str, Any]], str | None] | None = None


_NAMED_ENTITY_IDS = _Edit(
    "department/category/subcategory/topic/language entities and record_group "
    "entities (a Drive folder, Jira project, Slack channel, ...; restricts to "
    "that group's records). Nothing",
    "department/category/subcategory/topic/language entities, record_group "
    "entities (a Drive folder, Jira project, Slack channel, ...; restricts to "
    "that group's records) and named entities (an organization, product, event, "
    "...; restricts to the records that mention it). Nothing",
)

_VARIANTS: dict[str, _Variant] = {
    SEARCH_TOOL_NAME: _Variant(parameters=(("entity_ids", (_NAMED_ENTITY_IDS,)),)),
    SEARCH_ENTITIES_TOOL_NAME: _Variant(
        description=(
            _Edit(
                "and record titles. Only entities backed by records you can "
                "access are returned.",
                "and record titles, and the organizations, products, events, places and other "
                "named things mentioned inside documents (entityType 'named_entity'; narrow with "
                "named_entity_kinds). People are found by name with "
                "knowledgegraph__search_by_entities. Only entities backed by records you can "
                "access are returned.",
            ),
            _Edit(
                "a project or space, a document title — to find the matching entity, alongside or "
                "before a content search.",
                "a project or space, a document title, a customer or product — to find the "
                "matching entity, alongside or before a content search. A named entity on a few "
                "records narrows the search well; one on most records narrows nothing.",
            ),
            _Edit(
                "department/category/subcategory/topic/language or record_group entity.",
                "department/category/subcategory/topic/language, record_group or named entity.",
            ),
        ),
        parameters=(
            ("entity_types", (_Edit("'record_group', 'record'. Omit", "'record_group', 'record', 'named_entity'. Omit"),)),
            ("named_entity_kinds", (_Edit(
                "Optional kinds when searching extracted named entities "
                "(person, organization, product, date, currency, ...). "
                "Ignored unless named-entity extraction is enabled.",
                "Optional kinds when searching extracted named entities: organization, product, "
                "event, skill, person_type, location, city, state, country_region. People and "
                "addresses are not in this search (look them up by name with "
                "knowledgegraph__search_by_entities), nor dates and amounts (use "
                "knowledgegraph__find_records_by_value).",
            ),)),
        ),
        method="_search_entities_with_queries",
        extra_parameters=(
            ToolParameter(
                name="queries",
                type=ParameterType.ARRAY,
                description=(
                    "Several names or descriptions to look up in one call (up to 5), instead of one "
                    "call each; the result groups the matches under each query."
                ),
                required=False,
                items={"type": "string"},
            ),
        ),
        optional=("query",),
        args_summary=lambda args: _searched_entities_for(args),
    ),
    FIND_RECORDS_BY_ENTITY_TOOL_NAME: _Variant(
        description=(
            _Edit(
                "every record directly inside a record_group, or the record itself",
                "every record directly inside a record_group, every record that mentions a "
                "named entity, or the record itself",
            ),
        ),
        parameters=(
            ("entity_type", (_Edit(
                "'language', 'record_group' or 'record'. Optional",
                "'language', 'record_group', 'record' or 'named_entity'. Optional",
            ),)),
        ),
    ),
    FIND_RECORDS_BY_VALUE_TOOL_NAME: _Variant(
        description=(
            _Edit(
                "an amount in a currency, a quantity, or a percentage. Requires",
                "an amount in a currency, a quantity, or a percentage. Use it when the question "
                "is about a value rather than words (\"deals over $50k\", \"incidents in March "
                "2026\", \"latency above 200 ms\"): it finds a record however the value is "
                "written there. Requires",
            ),
        ),
    ),
}


def tools_for_request(instance: Any, tools: list[Tool], *, offered: bool) -> list[Tool]:  # noqa: ANN401
    """``tools`` as declared, without ``search_by_entities`` unless ``offered``;
    with it, the entity-search variants in place of their declared tools."""
    if not offered:
        return [tool for tool in tools if tool.name != SEARCH_BY_ENTITIES_TOOL_NAME]
    return [_variant_tool(instance, tool) for tool in tools]


def _variant_tool(instance: Any, tool: Tool) -> Tool:  # noqa: ANN401
    variant = _VARIANTS.get(tool.name)
    declared = getattr(getattr(type(instance), _method_name(tool), None), TOOL_META_ATTR, None)
    if variant is None or declared is None:
        return tool
    method: Callable[..., Any] = getattr(instance, variant.method or _method_name(tool))
    return BoundMethodTool(method, variant_meta(tool.name, declared))


def _method_name(tool: Tool) -> str:
    return tool.name.split("__", 1)[-1]


def variant_meta(tool_name: str, declared: ToolMeta) -> ToolMeta:
    variant = _VARIANTS[tool_name]
    edits = dict(variant.parameters)
    parameters = [
        replace(
            parameter,
            description=_edited(parameter.description, edits.get(parameter.name, ()), f"{tool_name}.{parameter.name}"),
            required=parameter.required and parameter.name not in variant.optional,
        )
        for parameter in declared.parameters
    ]
    missing = set(edits) - {parameter.name for parameter in declared.parameters}
    if missing:
        raise ValueError(f"{tool_name}: no parameter {sorted(missing)} to edit")
    return replace(
        declared,
        description=_edited(declared.description, variant.description, tool_name),
        parameters=[*parameters, *variant.extra_parameters],
        args_summary=variant.args_summary or declared.args_summary,
    )


def _searched_entities_for(args: dict[str, Any]) -> str | None:
    asked = [q for q in [args.get("query"), *(args.get("queries") or [])] if isinstance(q, str) and q.strip()]
    return "Searched entities for " + ", ".join(f'"{q}"' for q in dict.fromkeys(asked)) if asked else None


def _edited(text: str, edits: tuple[_Edit, ...], where: str) -> str:
    for edit in edits:
        if text.count(edit.old) != 1:
            raise ValueError(f"{where}: the declared text no longer reads {edit.old!r}")
        text = text.replace(edit.old, edit.new, 1)
    return text


__all__ = ["tools_for_request", "variant_meta"]
